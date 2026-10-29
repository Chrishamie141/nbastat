"""Auditable NBA roster, injury, and player-availability ingestion.

The feed is deliberately isolated from prediction generation.  It records what
the provider reported, when it was observed, and whether it is still fresh;
missing injury rows are never interpreted as proof that a player is available.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
from threading import Lock
from typing import Any, Callable

import requests

from backend.app.database import get_db_connection, table_exists, using_postgres
from .season import normalize_nba_season

ESPN_TEAMS = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"
ESPN_ROSTER = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team}/roster"
ROSTER_MAX_AGE_SECONDS = 36 * 3600
AVAILABILITY_MAX_AGE_SECONDS = 8 * 3600
EXPECTED_NBA_TEAMS = 30
PERSONNEL_REFRESH_LOCK_KEY = 1_782_602_516
logger = logging.getLogger(__name__)
_sqlite_refresh_lock = Lock()


@contextmanager
def personnel_refresh_guard():
    """Prevent concurrent personnel refreshes across processes and test workers.

    PostgreSQL advisory locks are session-scoped, so the dedicated connection is
    held for the complete provider refresh and releases the lock automatically if
    the process exits. SQLite is local-only in this project, where a process lock
    is sufficient for development and unit tests.
    """
    if not using_postgres():
        acquired = _sqlite_refresh_lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                _sqlite_refresh_lock.release()
        return

    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT pg_try_advisory_lock(?) AS acquired",
            (PERSONNEL_REFRESH_LOCK_KEY,),
        ).fetchone()
        acquired = bool(row and row["acquired"])
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(
                    "SELECT pg_advisory_unlock(?)",
                    (PERSONNEL_REFRESH_LOCK_KEY,),
                )


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dt(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: Any, *, fallback: str | None = None) -> str:
    if value in (None, ""):
        if fallback is None:
            raise ValueError("provider timestamp is missing")
        value = fallback
    return _dt(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _freshness(observed_at: str | None, *, max_age_seconds: int,
               at: datetime | None = None) -> dict[str, Any]:
    if not observed_at:
        return {"status": "MISSING", "ageSeconds": None, "maxAgeSeconds": max_age_seconds}
    clock = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        age = max(0, int((clock - _dt(observed_at)).total_seconds()))
    except (TypeError, ValueError):
        return {"status": "ERROR", "ageSeconds": None, "maxAgeSeconds": max_age_seconds}
    return {"status": "FRESH" if age <= max_age_seconds else "STALE",
            "ageSeconds": age, "maxAgeSeconds": max_age_seconds}


def initialize() -> None:
    if using_postgres():
        required = ("nba_player_availability_snapshots", "nba_data_source_status")
        with get_db_connection() as connection:
            missing = [
                table for table in required
                if not table_exists(connection, table, postgres=True)
            ]
        if missing:
            raise RuntimeError("NBA personnel migration is not installed")
        return
    with get_db_connection() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS nba_player_availability_snapshots (
            snapshot_id TEXT PRIMARY KEY, season_code TEXT NOT NULL, team_code TEXT NOT NULL,
            athlete_id TEXT NOT NULL, player_name TEXT NOT NULL, position TEXT,
            jersey TEXT, roster_status TEXT NOT NULL, injury_status TEXT NOT NULL,
            availability_status TEXT NOT NULL, injury_detail TEXT, injury_observed_at TEXT,
            provider TEXT NOT NULL, provider_observed_at TEXT NOT NULL,
            retrieved_at TEXT NOT NULL, source_hash TEXT NOT NULL, payload_json TEXT NOT NULL)""")
        connection.execute("""CREATE INDEX IF NOT EXISTS idx_nba_availability_current
            ON nba_player_availability_snapshots(season_code,team_code,athlete_id,provider_observed_at)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS nba_data_source_status (
            source_key TEXT PRIMARY KEY, season_code TEXT NOT NULL, data_kind TEXT NOT NULL,
            team_code TEXT, provider TEXT NOT NULL, last_attempt_at TEXT NOT NULL,
            last_success_at TEXT, provider_observed_at TEXT, max_age_seconds INTEGER NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL,
            error_code TEXT, details_json TEXT NOT NULL DEFAULT '{}')""")
        connection.execute("""CREATE INDEX IF NOT EXISTS idx_nba_source_status_kind
            ON nba_data_source_status(season_code,data_kind,state)""")
        for operation in ("UPDATE", "DELETE"):
            name = f"nba_player_availability_snapshots_immutable_{operation.lower()}"
            connection.execute(f"""CREATE TRIGGER IF NOT EXISTS {name}
                BEFORE {operation} ON nba_player_availability_snapshots
                BEGIN SELECT RAISE(ABORT,'immutable NBA personnel evidence'); END""")


def _injury_state(injuries: list[dict], roster_status: str) -> tuple[str, str, str | None, str | None]:
    if injuries:
        injury = max(injuries, key=lambda row: str(row.get("date") or ""))
        raw = str(injury.get("status") or injury.get("type") or "UNKNOWN").strip().upper()
        if "OUT" in raw or "INACTIVE" in raw:
            availability = "OUT"
        elif "DOUBT" in raw:
            availability = "DOUBTFUL"
        elif "QUESTION" in raw or "DAY-TO-DAY" in raw or "DAY TO DAY" in raw:
            availability = "QUESTIONABLE"
        elif "PROB" in raw:
            availability = "PROBABLE"
        elif "ACTIVE" in raw or "AVAILABLE" in raw:
            availability = "AVAILABLE"
        else:
            availability = "UNKNOWN"
        detail = injury.get("details") or injury.get("description") or injury.get("type")
        return raw or "UNKNOWN", availability, None if detail is None else str(detail), injury.get("date")
    roster_upper = roster_status.upper()
    if "INACTIVE" in roster_upper or "SUSPEND" in roster_upper:
        return "NOT_REPORTED", "ROSTER_INACTIVE", None, None
    # An empty provider injury list is not evidence of game availability.
    return "NOT_REPORTED", "NO_INJURY_REPORTED", None, None


def normalize_roster_payload(*, team_code: str, season: str | int, payload: dict,
                             retrieved_at: str | None = None) -> tuple[list[dict], dict]:
    season_code = normalize_nba_season(season).code
    retrieved = _iso(retrieved_at or _now())
    observed = _iso(payload.get("timestamp"), fallback=retrieved)
    provider_season = str((payload.get("season") or {}).get("displayName") or "")
    if provider_season and provider_season != season_code:
        raise ValueError(f"provider season mismatch: expected {season_code}, received {provider_season}")
    athletes = payload.get("athletes")
    if not isinstance(athletes, list) or not athletes:
        raise ValueError("provider roster contains no athletes")
    rows = []
    for athlete in athletes:
        athlete_id = str(athlete.get("id") or "").strip()
        player_name = str(athlete.get("displayName") or athlete.get("fullName") or "").strip()
        if not athlete_id or not player_name:
            continue
        status = athlete.get("status") or {}
        roster_status = str(status.get("name") or status.get("type") or "UNKNOWN").upper()
        injury_status, availability, detail, injury_at = _injury_state(
            athlete.get("injuries") or [], roster_status
        )
        source = {
            "season": season_code, "team": str(team_code).upper(), "athlete": athlete,
            "providerObservedAt": observed,
        }
        source_hash = _hash(source)
        rows.append({
            "snapshot_id": f"nba-availability-{source_hash[:32]}",
            "season_code": season_code, "team_code": str(team_code).upper(),
            "athlete_id": athlete_id, "player_name": player_name,
            "position": (athlete.get("position") or {}).get("abbreviation"),
            "jersey": athlete.get("jersey"), "roster_status": roster_status,
            "injury_status": injury_status, "availability_status": availability,
            "injury_detail": detail,
            "injury_observed_at": _iso(injury_at) if injury_at else None,
            "provider": "espn", "provider_observed_at": observed,
            "retrieved_at": retrieved, "source_hash": source_hash,
            "payload_json": _canonical(athlete),
        })
    if not rows:
        raise ValueError("provider roster contains no identifiable athletes")
    return rows, {"provider": "espn", "providerObservedAt": observed,
                  "retrievedAt": retrieved, "rowCount": len(rows)}


def _record_status(*, source_key: str, season_code: str, data_kind: str,
                   team_code: str | None, attempted_at: str, state: str,
                   max_age_seconds: int, row_count: int = 0,
                   provider_observed_at: str | None = None,
                   error_code: str | None = None, details: dict | None = None) -> None:
    success = attempted_at if state == "FRESH" else None
    with get_db_connection() as connection:
        connection.execute("""INSERT INTO nba_data_source_status
            (source_key,season_code,data_kind,team_code,provider,last_attempt_at,last_success_at,
             provider_observed_at,max_age_seconds,row_count,state,error_code,details_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET
              season_code=excluded.season_code,data_kind=excluded.data_kind,
              team_code=excluded.team_code,provider=excluded.provider,
              last_attempt_at=excluded.last_attempt_at,
              last_success_at=CASE WHEN excluded.state='FRESH' THEN excluded.last_success_at
                                   ELSE nba_data_source_status.last_success_at END,
              provider_observed_at=CASE WHEN excluded.state='FRESH' THEN excluded.provider_observed_at
                                        ELSE nba_data_source_status.provider_observed_at END,
              max_age_seconds=excluded.max_age_seconds,
              row_count=CASE WHEN excluded.state='FRESH' THEN excluded.row_count
                             ELSE nba_data_source_status.row_count END,
              state=excluded.state,error_code=excluded.error_code,
              details_json=excluded.details_json""",
            (source_key, season_code, data_kind, team_code, "espn", attempted_at, success,
             provider_observed_at, int(max_age_seconds), int(row_count), state,
             error_code, _canonical(details or {})))


def _team_codes(payload: dict) -> list[str]:
    groups = payload.get("sports") or []
    leagues = (groups[0].get("leagues") or []) if groups else []
    teams = (leagues[0].get("teams") or []) if leagues else []
    codes = [str((row.get("team") or {}).get("abbreviation") or "").upper() for row in teams]
    return sorted({code for code in codes if code})


def _get_json(http_get: Callable, url: str, *, timeout: float) -> dict:
    response = http_get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("provider response is not an object")
    return payload


def refresh_personnel(*, season: str | int, http_get: Callable = requests.get,
                      timeout: float = 10.0, max_workers: int = 8,
                      team_codes: list[str] | None = None) -> dict:
    """Refresh all NBA rosters and embedded injury reports without model changes."""
    initialize()
    season_code = normalize_nba_season(season).code
    attempted_at = _now()
    if team_codes is None:
        try:
            team_codes = _team_codes(_get_json(http_get, ESPN_TEAMS + "?limit=100", timeout=timeout))
            if len(team_codes) < EXPECTED_NBA_TEAMS:
                raise ValueError(f"provider returned only {len(team_codes)} NBA teams")
            _record_status(source_key=f"espn-teams:{season_code}", season_code=season_code,
                           data_kind="teams", team_code=None, attempted_at=attempted_at,
                           state="FRESH", max_age_seconds=ROSTER_MAX_AGE_SECONDS,
                           row_count=len(team_codes), provider_observed_at=attempted_at)
        except Exception as exc:
            _record_status(source_key=f"espn-teams:{season_code}", season_code=season_code,
                           data_kind="teams", team_code=None, attempted_at=attempted_at,
                           state="ERROR", max_age_seconds=ROSTER_MAX_AGE_SECONDS,
                           error_code=type(exc).__name__)
            logger.warning("nba_personnel_team_list_failed error=%s", type(exc).__name__)
            return {"status": "ERROR", "season": season_code, "teamsAttempted": 0,
                    "teamsSucceeded": 0, "playersStored": 0,
                    "errors": [{"team": None, "errorCode": type(exc).__name__}]}
    team_codes = sorted({str(code).upper() for code in team_codes if str(code).strip()})

    def fetch(team: str) -> tuple[str, list[dict], dict]:
        payload = _get_json(http_get, ESPN_ROSTER.format(team=team.lower()), timeout=timeout)
        rows, metadata = normalize_roster_payload(
            team_code=team, season=season_code, payload=payload, retrieved_at=_now()
        )
        return team, rows, metadata

    successes, errors, stored = 0, [], 0
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(team_codes) or 1))) as pool:
        futures = {pool.submit(fetch, team): team for team in team_codes}
        for future in as_completed(futures):
            team = futures[future]
            try:
                _, rows, metadata = future.result()
                with get_db_connection() as connection:
                    for row in rows:
                        connection.execute("""INSERT INTO nba_player_availability_snapshots
                            (snapshot_id,season_code,team_code,athlete_id,player_name,position,jersey,
                             roster_status,injury_status,availability_status,injury_detail,
                             injury_observed_at,provider,provider_observed_at,retrieved_at,
                             source_hash,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(snapshot_id) DO NOTHING""", (
                                row["snapshot_id"], row["season_code"], row["team_code"],
                                row["athlete_id"], row["player_name"], row["position"], row["jersey"],
                                row["roster_status"], row["injury_status"], row["availability_status"],
                                row["injury_detail"], row["injury_observed_at"], row["provider"],
                                row["provider_observed_at"], row["retrieved_at"], row["source_hash"],
                                row["payload_json"],
                            ))
                for kind, maximum in (("roster", ROSTER_MAX_AGE_SECONDS),
                                      ("availability", AVAILABILITY_MAX_AGE_SECONDS)):
                    _record_status(source_key=f"espn-{kind}:{season_code}:{team}",
                                   season_code=season_code, data_kind=kind, team_code=team,
                                   attempted_at=attempted_at, state="FRESH",
                                   max_age_seconds=maximum, row_count=len(rows),
                                   provider_observed_at=metadata["providerObservedAt"])
                successes += 1
                stored += len(rows)
            except Exception as exc:
                errors.append({"team": team, "errorCode": type(exc).__name__})
                for kind, maximum in (("roster", ROSTER_MAX_AGE_SECONDS),
                                      ("availability", AVAILABILITY_MAX_AGE_SECONDS)):
                    _record_status(source_key=f"espn-{kind}:{season_code}:{team}",
                                   season_code=season_code, data_kind=kind, team_code=team,
                                   attempted_at=attempted_at, state="ERROR",
                                   max_age_seconds=maximum, error_code=type(exc).__name__)
                logger.warning("nba_personnel_team_failed team=%s error=%s", team, type(exc).__name__)
    status = "FRESH" if successes == len(team_codes) else "PARTIAL" if successes else "ERROR"
    return {"status": status, "season": season_code, "teamsAttempted": len(team_codes),
            "teamsSucceeded": successes, "playersStored": stored, "errors": errors}


def personnel_health(season: str | int, *, at: datetime | None = None) -> dict:
    initialize()
    season_code = normalize_nba_season(season).code
    with get_db_connection() as connection:
        rows = [dict(row) for row in connection.execute(
            "SELECT * FROM nba_data_source_status WHERE season_code=? ORDER BY data_kind,team_code",
            (season_code,),
        ).fetchall()]
    expected_row = next((row for row in rows if row["data_kind"] == "teams"), None)
    expected = int(expected_row["row_count"]) if expected_row and expected_row.get("row_count") else EXPECTED_NBA_TEAMS
    result: dict[str, Any] = {"season": season_code, "provider": "espn", "expectedTeams": expected}
    for kind in ("roster", "availability"):
        source_rows = [row for row in rows if row["data_kind"] == kind]
        items = []
        for row in source_rows:
            fresh = _freshness(row.get("provider_observed_at"),
                               max_age_seconds=int(row["max_age_seconds"]), at=at)
            if row.get("state") == "ERROR" and not row.get("last_success_at"):
                fresh["status"] = "ERROR"
            items.append({"team": row.get("team_code"), **fresh,
                          "providerObservedAt": row.get("provider_observed_at"),
                          "lastAttemptAt": row.get("last_attempt_at"),
                          "lastSuccessAt": row.get("last_success_at"),
                          "rowCount": int(row.get("row_count") or 0),
                          "errorCode": row.get("error_code")})
        counts = {state: sum(item["status"] == state for item in items)
                  for state in ("FRESH", "STALE", "MISSING", "ERROR")}
        missing_teams = max(0, expected - len(items))
        counts["MISSING"] += missing_teams
        if counts["ERROR"]:
            state = "ERROR"
        elif counts["MISSING"]:
            state = "MISSING"
        elif counts["STALE"]:
            state = "STALE"
        else:
            state = "FRESH"
        result[kind] = {"status": state, "counts": counts, "teamsReported": len(items),
                        "missingTeams": missing_teams, "items": items}
    return result


def current_availability(*, season: str | int, team_code: str | None = None) -> list[dict]:
    """Return each athlete's most recently observed immutable personnel row."""
    initialize()
    season_code = normalize_nba_season(season).code
    where, values = "season_code=?", [season_code]
    if team_code:
        where += " AND team_code=?"
        values.append(str(team_code).upper())
    with get_db_connection() as connection:
        rows = connection.execute(f"""SELECT * FROM nba_player_availability_snapshots
            WHERE {where} ORDER BY provider_observed_at DESC,snapshot_id DESC""", tuple(values)).fetchall()
    latest: dict[tuple[str, str], dict] = {}
    for raw in rows:
        row = dict(raw)
        latest.setdefault((row["team_code"], row["athlete_id"]), row)
    return list(latest.values())
