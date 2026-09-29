"""Account-owned confirmed parlay tickets with immutable leg snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from backend.app.database import get_db_connection, using_postgres
from nfl_parlay_grader import (
    TEAM_MARKET_ALIASES,
    _actual_player_value,
    _grade_player_leg,
    _grade_team_leg,
)

TICKET_TYPES = {"SAME_GAME_PARLAY", "MULTI_GAME_PARLAY"}
STRATEGIES = {"SAFE", "BALANCED", "AGGRESSIVE"}
RECOMMENDATION_STATES = {
    "SMARTBET_RECOMMENDED", "SMARTBET_LEAN", "NEUTRAL", "NO_BET", "MANUAL_SELECTION",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def initialize_parlay_ticket_database() -> None:
    with get_db_connection() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS parlay_tickets (
            ticket_id TEXT PRIMARY KEY, user_id BIGINT NOT NULL, ticket_type TEXT NOT NULL,
            sportsbook TEXT NOT NULL, strategy TEXT NOT NULL, season INTEGER NOT NULL,
            season_type TEXT NOT NULL, week INTEGER NOT NULL, stake REAL,
            combined_odds INTEGER, potential_payout REAL, number_of_legs INTEGER NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, confirmed_at TEXT NOT NULL,
            graded_at TEXT, source_hash TEXT NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS parlay_ticket_legs (
            ticket_leg_id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, leg_index INTEGER NOT NULL,
            game_id TEXT NOT NULL, sportsbook TEXT NOT NULL, player_id TEXT, player_name TEXT,
            team TEXT, opponent TEXT, matchup TEXT NOT NULL, market TEXT NOT NULL,
            selection TEXT NOT NULL, line REAL, odds INTEGER, smartbet_projection REAL,
            smartbet_probability REAL, market_implied_probability REAL, edge REAL,
            confidence REAL, strategy TEXT NOT NULL, recommendation_state TEXT NOT NULL,
            model_version TEXT, season INTEGER NOT NULL, week INTEGER NOT NULL,
            selected_at TEXT NOT NULL, final_result REAL, leg_status TEXT NOT NULL,
            source_row_id TEXT NOT NULL, source_snapshot_json TEXT NOT NULL,
            UNIQUE(ticket_id, leg_index), UNIQUE(ticket_id, source_row_id)
        )""")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_parlay_tickets_user_created ON parlay_tickets(user_id,confirmed_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_parlay_ticket_legs_game_status ON parlay_ticket_legs(game_id,leg_status)")
        if using_postgres():
            for table in ("parlay_tickets", "parlay_ticket_legs"):
                connection.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
                connection.execute(f"REVOKE ALL ON TABLE {table} FROM anon, authenticated")


def _number(value: Any, *, integer: bool = False):
    if value in (None, ""):
        return None
    try:
        return int(value) if integer else float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Ticket contains an invalid numeric value.") from exc


def _validate(ticket: dict[str, Any], legs: list[dict[str, Any]]) -> None:
    if ticket.get("ticket_type") not in TICKET_TYPES:
        raise ValueError("Unsupported parlay ticket type.")
    if ticket.get("strategy") not in STRATEGIES:
        raise ValueError("Unsupported parlay strategy.")
    if not str(ticket.get("sportsbook") or "").strip():
        raise ValueError("Choose a sportsbook before confirming the ticket.")
    if not 2 <= len(legs) <= 16:
        raise ValueError("A confirmed parlay must contain between 2 and 16 legs.")
    game_ids = {str(leg.get("game_id") or "") for leg in legs}
    if "" in game_ids:
        raise ValueError("Every ticket leg must identify a game.")
    if ticket["ticket_type"] == "SAME_GAME_PARLAY" and len(game_ids) != 1:
        raise ValueError("A same-game parlay may only contain one matchup.")
    source_ids = [str(leg.get("source_row_id") or "") for leg in legs]
    if any(not value for value in source_ids) or len(source_ids) != len(set(source_ids)):
        raise ValueError("Duplicate or unidentified ticket legs are not allowed.")
    conflicts: dict[tuple[str, str, str, Any], str] = {}
    for leg in legs:
        key = (str(leg["game_id"]), str(leg.get("player_id") or leg.get("player_name") or leg.get("team") or "").casefold(),
               str(leg.get("market") or "").upper(), leg.get("line"))
        selection = str(leg.get("selection") or "").upper()
        if key in conflicts and conflicts[key] != selection:
            raise ValueError("Conflicting selections for the same player, market, and line are not allowed.")
        conflicts[key] = selection
        if leg.get("recommendation_state") not in RECOMMENDATION_STATES:
            raise ValueError("A ticket leg has an invalid recommendation state.")


def create_ticket(*, user_id: int, ticket: dict[str, Any], legs: list[dict[str, Any]]) -> dict[str, Any]:
    initialize_parlay_ticket_database()
    _validate(ticket, legs)
    ticket_id = str(uuid4())
    confirmed = _now()
    stake = _number(ticket.get("stake"))
    combined_odds = _number(ticket.get("combined_odds"), integer=True)
    potential = None
    if stake is not None and combined_odds is not None:
        if stake <= 0 or combined_odds == 0:
            raise ValueError("Stake must be positive and combined odds cannot be zero.")
        profit = stake * (combined_odds / 100 if combined_odds > 0 else 100 / abs(combined_odds))
        potential = round(stake + profit, 2)
    persisted_ticket = {
        **ticket,
        "stake": stake,
        "combined_odds": combined_odds,
        "potential_payout": potential,
    }
    snapshot = {"ticket": persisted_ticket, "legs": legs}
    source_hash = hashlib.sha256(_json(snapshot).encode()).hexdigest()
    with get_db_connection() as connection:
        connection.execute("""INSERT INTO parlay_tickets
            (ticket_id,user_id,ticket_type,sportsbook,strategy,season,season_type,week,
             stake,combined_odds,potential_payout,number_of_legs,status,created_at,
             confirmed_at,graded_at,source_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                ticket_id, int(user_id), ticket["ticket_type"], ticket["sportsbook"],
                ticket["strategy"], int(ticket["season"]), ticket["season_type"],
                int(ticket["week"]), stake, combined_odds, potential, len(legs), "ACTIVE",
                confirmed, confirmed, None, source_hash,
            ))
        for index, leg in enumerate(legs):
            connection.execute("""INSERT INTO parlay_ticket_legs
                (ticket_leg_id,ticket_id,leg_index,game_id,sportsbook,player_id,player_name,
                 team,opponent,matchup,market,selection,line,odds,smartbet_projection,
                 smartbet_probability,market_implied_probability,edge,confidence,strategy,
                 recommendation_state,model_version,season,week,selected_at,final_result,
                 leg_status,source_row_id,source_snapshot_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    str(uuid4()), ticket_id, index, leg["game_id"], leg["sportsbook"],
                    leg.get("player_id"), leg.get("player_name"), leg.get("team"),
                    leg.get("opponent"), leg["matchup"], leg["market"], leg["selection"],
                    _number(leg.get("line")), _number(leg.get("odds"), integer=True),
                    _number(leg.get("smartbet_projection")), _number(leg.get("smartbet_probability")),
                    _number(leg.get("market_implied_probability")), _number(leg.get("edge")),
                    _number(leg.get("confidence")), leg["strategy"], leg["recommendation_state"],
                    leg.get("model_version"), int(leg["season"]), int(leg["week"]),
                    leg["selected_at"], None, "UNGRADED", leg["source_row_id"], _json(leg),
                ))
    return get_ticket(user_id=user_id, ticket_id=ticket_id)


def _ticket_record(row: Any, legs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = dict(row)
    result["legs"] = legs or []
    return result


def list_tickets(*, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    initialize_parlay_ticket_database()
    with get_db_connection() as connection:
        rows = connection.execute("""SELECT * FROM parlay_tickets WHERE user_id=?
            ORDER BY confirmed_at DESC,ticket_id DESC LIMIT ?""", (int(user_id), max(1, min(int(limit), 200)))).fetchall()
        result = []
        for row in rows:
            legs = [dict(leg) for leg in connection.execute(
                "SELECT * FROM parlay_ticket_legs WHERE ticket_id=? ORDER BY leg_index", (row["ticket_id"],)
            ).fetchall()]
            result.append(_ticket_record(row, legs))
    return result


def get_ticket(*, user_id: int, ticket_id: str) -> dict[str, Any]:
    initialize_parlay_ticket_database()
    with get_db_connection() as connection:
        row = connection.execute("SELECT * FROM parlay_tickets WHERE ticket_id=? AND user_id=?", (ticket_id, int(user_id))).fetchone()
        if not row:
            raise KeyError("ticket not found")
        legs = [dict(leg) for leg in connection.execute(
            "SELECT * FROM parlay_ticket_legs WHERE ticket_id=? ORDER BY leg_index", (ticket_id,)
        ).fetchall()]
    return _ticket_record(row, legs)


def grade_tickets(*, season: int, season_type: str, week: int) -> dict[str, int]:
    """Idempotently settle legs and safely apply official stat corrections."""
    initialize_parlay_ticket_database()
    graded = pending = 0
    with get_db_connection() as connection:
        results = {row["game_id"]: row for row in connection.execute(
            "SELECT * FROM nfl_game_result_snapshots WHERE season=? AND season_type=? AND week=?",
            (season, season_type, week),
        ).fetchall()}
        tickets = connection.execute("""SELECT * FROM parlay_tickets
            WHERE season=? AND season_type=? AND week=?""",
            (season, season_type, week)).fetchall()
        for ticket in tickets:
            legs = connection.execute("SELECT * FROM parlay_ticket_legs WHERE ticket_id=? ORDER BY leg_index", (ticket["ticket_id"],)).fetchall()
            statuses = []
            for row in legs:
                leg = json.loads(row["source_snapshot_json"])
                final = results.get(row["game_id"])
                if not final:
                    status, actual = "UNGRADED", None
                else:
                    player_stats = json.loads(final["player_stats_json"] or "{}")
                    market = TEAM_MARKET_ALIASES.get(str(leg.get("market") or "").upper())
                    if market:
                        home, away = final["home_team"], final["away_team"]
                        hs, as_ = final["home_score"], final["away_score"]
                        team_results = {
                            home: {"won": hs > as_, "tied": hs == as_, "margin": hs-as_, "total": hs+as_},
                            away: {"won": as_ > hs, "tied": hs == as_, "margin": as_-hs, "total": hs+as_},
                        }
                        raw = _grade_team_leg({**leg, "stat_type": market}, team_results)
                        team_result = team_results.get(str(leg.get("team") or "").upper())
                        actual = (
                            team_result.get("margin") if market == "SPREAD" and team_result else
                            team_result.get("total") if market == "TOTAL" and team_result else
                            1.0 if market == "MONEYLINE" and team_result and team_result.get("won") else
                            0.0 if market == "MONEYLINE" and team_result and team_result.get("won") is False else
                            None
                        )
                    else:
                        grading_leg = {**leg, "player": leg.get("player_name"),
                                       "stat_type": leg.get("market"),
                                       "side": leg.get("selection")}
                        raw = _grade_player_leg(grading_leg, player_stats)
                        actual = _actual_player_value(grading_leg, player_stats)
                    status = {"hit": "WON", "missed": "LOST", "push": "PUSH", "void": "VOID", "pending": "UNGRADED"}[raw]
                statuses.append(status)
                connection.execute("UPDATE parlay_ticket_legs SET leg_status=?,final_result=? WHERE ticket_leg_id=?", (status, actual, row["ticket_leg_id"]))
            if "LOST" in statuses:
                overall = "LOST"
            elif "UNGRADED" in statuses:
                overall = "PARTIAL" if any(value != "UNGRADED" for value in statuses) else "PENDING"
            else:
                active = [value for value in statuses if value not in {"PUSH", "VOID"}]
                overall = "WON" if active and all(value == "WON" for value in active) else "PUSH" if "PUSH" in statuses else "VOID"
            done = overall in {"WON", "LOST", "PUSH", "VOID"}
            connection.execute("UPDATE parlay_tickets SET status=?,graded_at=? WHERE ticket_id=?", (overall, _now() if done else None, ticket["ticket_id"]))
            graded += int(done)
            pending += int(not done)
    return {"graded": graded, "pending": pending}
