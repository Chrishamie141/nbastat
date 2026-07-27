from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from backtesting.build_snapshots import main as build_main
from backtesting.nfl_game_predictor import NFLGameMarketPredictor


def observation(team, opponent, week, *, season=2024, game_id=None, points=24, allowed=20, known=None):
    stamp = known or f"{season}-09-{week + 7:02d}T23:00:00Z"
    return {
        "season": season, "week": week, "through_week": week, "team": team,
        "game_id": game_id or f"{season}-{week}-{team}", "opponent": opponent,
        "points_for": points, "points_against": allowed, "home_away": "home",
        "completed_at": stamp, "captured_at": stamp, "data_as_of": stamp,
        "record_role": "completed_game_history", "source": "fixture", "is_pregame": False,
    }


def test_prior_history_aliases_deduplication_and_leakage_diagnostics():
    game = {"game_id": "week1", "season": 2025, "week": 1, "home_team": "Buffalo Bills", "away_team": "Miami Dolphins", "kickoff_time": "2025-09-07T17:00:00Z"}
    rows = [observation("BUF", "MIA", week) for week in range(1, 5)]
    rows += [observation("Miami Dolphins", "BUF", week, points=20, allowed=24) for week in range(1, 5)]
    rows.append(dict(rows[0]))  # exact game observation duplicate
    rows.append(observation("BUF", "MIA", 1, season=2025, game_id="week1", known="2025-09-07T23:00:00Z"))
    predictor = NFLGameMarketPredictor()
    projection = predictor.project(game, rows)
    assert projection is not None
    assert projection.features["home_points_for"] == 24
    assert predictor.last_diagnostics["BUF"]["history_rows_used"] == 4
    assert predictor.last_diagnostics["BUF"]["seasons_used"] == [2024]
    assert predictor.last_diagnostics["BUF"]["rejected_future_rows"] == 1


def test_later_week_accepts_prior_completed_current_season_but_rejects_future():
    game = {"game_id": "week6", "season": 2025, "week": 6, "home_team": "BUF", "away_team": "MIA", "kickoff_time": "2025-10-12T17:00:00Z"}
    rows = [observation(team, "MIA" if team == "BUF" else "BUF", week, season=2025, known=f"2025-09-{week + 7:02d}T23:00:00Z") for team in ("BUF", "MIA") for week in range(1, 5)]
    rows += [observation(team, "X", 7, season=2025, known="2025-10-20T23:00:00Z") for team in ("BUF", "MIA")]
    predictor = NFLGameMarketPredictor()
    assert predictor.project(game, rows) is not None
    assert all(d["seasons_used"] == [2025] and d["rejected_future_rows"] == 1 for d in predictor.last_diagnostics.values())


def test_genuinely_insufficient_completed_game_history_is_rejected():
    game = {"season": 2025, "week": 1, "home_team": "BUF", "away_team": "MIA", "kickoff_time": "2025-09-07T17:00:00Z"}
    rows = [observation(team, "X", week) for team in ("BUF", "MIA") for week in range(1, 4)]
    assert NFLGameMarketPredictor().project(game, rows) is None


def test_team_stats_refresh_never_constructs_odds_source_and_preserves_odds(tmp_path, monkeypatch, capsys):
    week_dir = tmp_path / "nfl/2025/week_01"
    week_dir.mkdir(parents=True)
    (week_dir / "games.json").write_text(json.dumps([{"game_id": "g", "kickoff_time": "2025-09-07T17:00:00Z", "home_team": "BUF", "away_team": "MIA"}]))
    odds = b'[{"paid": true}]\n'
    (week_dir / "odds.json").write_bytes(odds)

    class FreeHistory:
        supported_datasets = {"team_stats"}
        def fetch_team_stats(self, *args):
            return [observation(team, "X", week) for team in ("BUF", "MIA") for week in range(1, 5)]

    monkeypatch.setattr("backtesting.build_snapshots.create_sources", lambda spec: ([FreeHistory()] if spec == "espn" else pytest.fail("odds source requested")))
    args = Namespace(league="nfl", season="2025", start_week=1, end_week=1, data_dir=tmp_path, refresh="team-stats", validate=False)
    assert build_main(args) == 0
    assert (week_dir / "odds.json").read_bytes() == odds
    assert "Historical odds preserved; Odds API not requested." in capsys.readouterr().out
