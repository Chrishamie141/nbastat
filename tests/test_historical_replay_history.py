from __future__ import annotations

import json
from pathlib import Path

from backtesting.historical_provider import HistoricalSnapshotProvider


def _write_rows(root: Path, season: int, week: int, rows: list[dict]) -> None:
    directory = root / "nfl" / str(season) / f"week_{week:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "team_stats.json").write_text(json.dumps(rows))


def _row(season: int, week: int, game_id: str, completed_at: str) -> dict:
    return {
        "season": season,
        "week": week,
        "game_id": game_id,
        "team": "BUF",
        "opponent": "MIA",
        "points_for": 24,
        "points_against": 17,
        "completed_at": completed_at,
        "data_as_of": completed_at,
        "record_role": "completed_game_history",
        "is_pregame": False,
    }


def test_team_stats_replay_view_uses_prior_weeks_not_target_week(tmp_path: Path):
    prior = _row(2025, 1, "prior-game", "2025-09-08T21:00:00Z")
    target_week = _row(2025, 2, "target-week-final", "2025-09-15T21:00:00Z")
    _write_rows(tmp_path, 2025, 1, [prior])
    _write_rows(tmp_path, 2025, 2, [target_week])

    rows = HistoricalSnapshotProvider(tmp_path).get_team_stats("nfl", "2025", 2)

    assert [row["game_id"] for row in rows] == ["prior-game"]


def test_week_one_replay_uses_available_prior_season_history(tmp_path: Path):
    prior = _row(2024, 18, "prior-season", "2025-01-06T04:00:00Z")
    current = _row(2025, 1, "current-season-week-one", "2025-09-08T21:00:00Z")
    _write_rows(tmp_path, 2024, 18, [prior])
    _write_rows(tmp_path, 2025, 1, [current])

    rows = HistoricalSnapshotProvider(tmp_path).get_team_stats("nfl", "2025", 1)

    assert [row["game_id"] for row in rows] == ["prior-season"]


def test_replay_history_deduplicates_repeated_immutable_snapshots(tmp_path: Path):
    duplicate = _row(2024, 18, "same-game", "2025-01-06T04:00:00Z")
    _write_rows(tmp_path, 2024, 17, [duplicate])
    _write_rows(tmp_path, 2024, 18, [duplicate])
    _write_rows(tmp_path, 2025, 1, [])

    rows = HistoricalSnapshotProvider(tmp_path).get_team_stats("nfl", "2025", 1)

    assert [row["game_id"] for row in rows] == ["same-game"]
