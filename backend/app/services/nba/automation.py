"""Idempotent NBA schedule/final synchronization and settlement cycle."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import logging
from threading import Lock
from typing import Callable

from backend.app.database import get_db_connection, using_postgres
from .lifecycle import (
    finish_automation_run,
    reconcile_persisted_finals,
    settle_game,
    start_automation_run,
    unresolved_past_predictions,
    upsert_game,
)
from .production import generate_winner_predictions
from .schedule import fetch_schedule
from .season import normalize_nba_season
from .availability import refresh_personnel
from .shadow import generate_shadow_predictions

logger = logging.getLogger(__name__)
_sqlite_result_lock = Lock()
_RESULT_RECONCILIATION_LOCK_ID = 748219308


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parsed(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@contextmanager
def _result_reconciliation_guard():
    """Prevent overlapping provider refreshes across serverless instances."""
    if not using_postgres():
        acquired = _sqlite_result_lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                _sqlite_result_lock.release()
        return
    connection = get_db_connection()
    acquired = False
    try:
        row = connection.execute(
            "SELECT pg_try_advisory_lock(?) AS acquired",
            (_RESULT_RECONCILIATION_LOCK_ID,),
        ).fetchone()
        acquired = bool(row["acquired"])
        yield acquired
    finally:
        try:
            if acquired:
                connection.execute(
                    "SELECT pg_advisory_unlock(?)",
                    (_RESULT_RECONCILIATION_LOCK_ID,),
                )
                connection.commit()
        finally:
            connection.close()


def _synchronize_results(*, season: str, start: date, end: date,
                         schedule_fetcher: Callable) -> dict:
    games = schedule_fetcher(season=season, start=start, end=end)
    changed = finals = settled = pending = already = 0
    for game in games:
        changed += int(upsert_game(game))
        if str(game.get("status") or "").upper() != "FINAL":
            continue
        finals += 1
        result = settle_game(game["game_id"])
        settled += int(result.get("settled") or 0)
        pending += int(result.get("pending") or 0)
        already += int(result.get("alreadySettled") or 0)
    recovery = reconcile_persisted_finals(season=season)
    return {
        "games": games,
        "gamesChanged": changed,
        "finals": finals,
        "predictionsSettled": settled + int(recovery["settled"]),
        "predictionsPendingData": pending + int(recovery["pending"]),
        "alreadySettled": already + int(recovery["alreadySettled"]),
        "recoverySweep": recovery,
    }


def maybe_reconcile_recent_results(
    *, season: str | int, schedule_fetcher: Callable = fetch_schedule,
    at: datetime | None = None, minimum_interval_minutes: int = 15,
    trigger_source: str = "history_auto_reconcile",
) -> dict:
    """Self-heal past unresolved picks without touching future predictions.

    The daily Vercel lifecycle remains the primary scheduler. This bounded,
    locked recovery path runs only when history or performance is read, only
    when a past pregame snapshot is unresolved, and at most once per interval.
    """
    normalized = normalize_nba_season(season)
    clock = (at or _utcnow()).astimezone(timezone.utc)
    unresolved = unresolved_past_predictions(season=normalized.code, at=clock)
    if not unresolved:
        return {"status": "CURRENT", "unresolvedPast": 0}
    with get_db_connection() as connection:
        recent = connection.execute(
            """SELECT completed_at FROM nba_automation_runs
               WHERE season_code=? AND trigger_source=? AND status='SUCCEEDED'
               ORDER BY completed_at DESC LIMIT 1""",
            (normalized.code, trigger_source),
        ).fetchone()
    if recent and recent["completed_at"]:
        age = clock - _parsed(recent["completed_at"])
        if age < timedelta(minutes=max(1, int(minimum_interval_minutes))):
            return {"status": "THROTTLED", "unresolvedPast": unresolved}
    with _result_reconciliation_guard() as acquired:
        if not acquired:
            return {"status": "SKIPPED_OVERLAP", "unresolvedPast": unresolved}
        unresolved = unresolved_past_predictions(season=normalized.code, at=clock)
        if not unresolved:
            return {"status": "CURRENT", "unresolvedPast": 0}
        run_id = start_automation_run(season=normalized.code, trigger_source=trigger_source)
        try:
            season_start = date(normalized.start_year, 10, 1)
            start = max(season_start, clock.date() - timedelta(days=45))
            result = _synchronize_results(
                season=normalized.code,
                start=start,
                end=clock.date(),
                schedule_fetcher=schedule_fetcher,
            )
            report = {
                "runId": run_id,
                "league": "NBA",
                "season": normalized.code,
                "window": {"start": start.isoformat(), "end": clock.date().isoformat()},
                "gamesFetched": len(result["games"]),
                "gamesChanged": result["gamesChanged"],
                "predictionsCreated": 0,
                "predictionsSettled": result["predictionsSettled"],
                "predictionsPendingData": result["predictionsPendingData"],
                "alreadySettled": result["alreadySettled"],
                "recoverySweep": result["recoverySweep"],
                "modelStatus": "RESEARCH_ONLY",
                "productionReady": False,
            }
            finish_automation_run(run_id, status="SUCCEEDED", report=report)
            return {"status": "SUCCEEDED", "unresolvedPast": unresolved, **report}
        except Exception as exc:
            finish_automation_run(run_id, status="FAILED", error_code=type(exc).__name__)
            logger.exception("nba_result_reconciliation_failed run_id=%s", run_id)
            raise


def run_cycle(
    *, season: str | int, start: date | None = None, end: date | None = None,
    schedule_fetcher: Callable = fetch_schedule,
    prediction_generator: Callable = generate_winner_predictions,
    shadow_generator: Callable = generate_shadow_predictions,
    personnel_refresher: Callable = refresh_personnel,
    trigger_source: str = "manual",
) -> dict:
    """Synchronize a bounded schedule range and settle every verified final.

    Team markets settle as soon as final scores exist. Player-stat predictions
    remain pending until a verified box-score adapter supplies their statistics.
    Missing data is never converted to a loss.
    """
    normalized = normalize_nba_season(season)
    today = _utcnow().date()
    season_start = date(normalized.start_year, 10, 1)
    # Always replay from the active season boundary. This is bounded by the
    # monthly provider adapter and recovers safely after long outages.
    start = start or season_start
    end = end or (today + timedelta(days=14))
    run_id = start_automation_run(season=normalized.code, trigger_source=trigger_source)
    try:
        results = _synchronize_results(
            season=normalized.code,
            start=start,
            end=end,
            schedule_fetcher=schedule_fetcher,
        )
        games = results["games"]
        # Personnel evidence is intentionally not an input to the deployed
        # research baseline.  Refresh failures stay visible in the run report
        # and Command Center without blocking schedule/result reconciliation.
        try:
            personnel = personnel_refresher(season=normalized.code)
        except Exception as exc:  # defensive boundary around an optional provider
            logger.exception("nba_personnel_refresh_failed error=%s", type(exc).__name__)
            personnel = {"status": "ERROR", "season": normalized.code,
                         "teamsAttempted": 0, "teamsSucceeded": 0,
                         "playersStored": 0,
                         "errors": [{"team": None, "errorCode": type(exc).__name__}]}
        generated = prediction_generator(season=normalized.code)
        shadow = shadow_generator(season=normalized.code)
        report = {"runId": run_id, "league": "NBA", "season": normalized.code,
                  "window": {"start": start.isoformat(), "end": end.isoformat()},
                  "gamesFetched": len(games), "gamesChanged": results["gamesChanged"],
                  "finals": results["finals"],
                  "predictionsCreated": int(generated.get("created") or 0),
                  "predictionsAlreadyPresent": int(generated.get("alreadyPresent") or 0),
                  "officialPredictionsCreated": int(
                      generated.get("officialPredictionsCreated") or 0
                  ),
                  "forecastsRefreshed": int(
                      generated.get("forecastSnapshotsCreated") or 0
                  ),
                  "forecastsUnchanged": int(generated.get("forecastsUnchanged") or 0),
                  "upcomingGamesEvaluated": int(
                      generated.get("upcomingGamesEvaluated") or 0
                  ),
                  "predictionModel": generated.get("modelVersion"),
                  "shadowPredictions": shadow,
                  "modelStatus": generated.get("modelStatus"),
                  "productionReady": False,
                  "predictionsSettled": results["predictionsSettled"],
                  "predictionsPendingData": results["predictionsPendingData"],
                  "alreadySettled": results["alreadySettled"],
                  "recoverySweep": results["recoverySweep"],
                  "personnel": personnel}
        finish_automation_run(run_id, status="SUCCEEDED", report=report)
        logger.info("nba_automation_cycle %s", report)
        return report
    except Exception as exc:
        finish_automation_run(run_id, status="FAILED", error_code=type(exc).__name__)
        logger.exception("nba_automation_cycle_failed run_id=%s error=%s", run_id, type(exc).__name__)
        raise
