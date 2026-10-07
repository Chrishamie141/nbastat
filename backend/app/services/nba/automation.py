"""Idempotent NBA schedule/final synchronization and settlement cycle."""

from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import Callable

from .lifecycle import settle_game, upsert_game
from .schedule import fetch_schedule
from .season import normalize_nba_season

logger = logging.getLogger(__name__)


def run_cycle(
    *, season: str | int, start: date | None = None, end: date | None = None,
    schedule_fetcher: Callable = fetch_schedule,
) -> dict:
    """Synchronize a bounded schedule range and settle every verified final.

    Team markets settle as soon as final scores exist. Player-stat predictions
    remain pending until a verified box-score adapter supplies their statistics.
    Missing data is never converted to a loss.
    """
    normalized = normalize_nba_season(season)
    start = start or (date.today() - timedelta(days=2))
    end = end or (date.today() + timedelta(days=8))
    games = schedule_fetcher(season=normalized.code, start=start, end=end)
    changed = finals = settled = pending = already = 0
    for game in games:
        changed += int(upsert_game(game))
        if game["status"] != "FINAL":
            continue
        finals += 1
        result = settle_game(game["game_id"])
        settled += result["settled"]
        pending += result["pending"]
        already += result["alreadySettled"]
    report = {"league": "NBA", "season": normalized.code, "gamesFetched": len(games),
              "gamesChanged": changed, "finals": finals, "predictionsSettled": settled,
              "predictionsPendingData": pending, "alreadySettled": already}
    logger.info("nba_automation_cycle %s", report)
    return report
