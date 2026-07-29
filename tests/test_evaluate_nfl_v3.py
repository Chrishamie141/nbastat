from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtesting.evaluate_nfl_v3 import evaluate, main
from backtesting.nfl_v3 import NFLV3Config
from backtesting.snapshots import SnapshotError


MODELS = ["nfl_game_baseline_v1", "nfl_game_baseline_v2", "nfl_game_baseline_v3"]


def _history(team: str, opponent: str) -> list[dict]:
    rows = []
    for week in range(1, 5):
        stamp = f"2024-09-{week + 1:02d}T20:00:00Z"
        rows.append({"team": team, "opponent": opponent, "season": 2024, "week": week,
            "through_week": week, "game_id": f"2024-{week}-{team}", "points_for": 20 + week,
            "points_against": 17 + week, "completed_at": stamp, "data_as_of": stamp,
            "captured_at": stamp, "record_role": "completed_game_history", "is_pregame": False,
            "home_away": "home" if week % 2 else "away"})
    return rows


def _write_week(root: Path, week: int, *, games: int = 1, complete: bool = True) -> None:
    directory = root / "nfl" / "2025" / f"week_{week:02d}"
    directory.mkdir(parents=True)
    game_rows, outcomes, odds = [], [], []
    for index in range(games):
        game_id = f"canonical-w{week}-{index}"
        kickoff = f"2025-09-{week + 6:02d}T17:00:00Z"
        game_rows.append({"game_id": game_id, "league": "nfl", "season": "2025", "week": week,
            "kickoff_time": kickoff, "home_team": "BUF", "away_team": "MIA"})
        outcomes.append({"provider_event_id": game_id, "home_team": "Buffalo Bills", "away_team": "Miami Dolphins",
            "final_home_score": 27 if complete or index else None, "final_away_score": 20 if complete or index else None,
            "completed": bool(complete or index), "completed_at": f"2025-09-{week + 6:02d}T21:00:00Z"})
        captured = f"2025-09-{week + 6:02d}T12:00:00Z"
        for market, selections in (("h2h", (("Buffalo Bills", -120, 0), ("Miami Dolphins", 110, 0))),
                                   ("spread", (("Buffalo Bills", -110, -3), ("Miami Dolphins", -110, 3))),
                                   ("total", (("over", -110, 45), ("under", -110, 45)))):
            for selection, price, line in selections:
                odds.append({"game_id": game_id, "market": market, "selection": selection, "odds": price,
                             "line": line, "sportsbook": "book", "captured_at": captured})
    history = _history("BUF", "MIA") + _history("MIA", "BUF")
    for name, value in (("games", game_rows), ("outcomes", outcomes), ("team_stats", history), ("odds", odds)):
        (directory / f"{name}.json").write_text(json.dumps(value))


def test_multiweek_snapshots_produce_nonzero_paired_chronological_rows(tmp_path: Path):
    _write_week(tmp_path, 1); _write_week(tmp_path, 2)
    report = evaluate(tmp_path, 2025, 1, 2, MODELS, NFLV3Config(), "DEVELOPMENT RESULT")
    assert report["eligibility"] == {
        **report["eligibility"], "snapshots_discovered": 2, "weeks_discovered": [1, 2],
        "games_loaded": 2, "games_with_complete_outcomes": 2,
        "games_with_usable_historical_features": 2,
        "predictions_generated_per_model": {model: 2 for model in MODELS},
        "games_evaluated_per_model": {model: 2 for model in MODELS},
    }
    assert {model: summary["games"] for model, summary in report["models"].items()} == {model: 2 for model in MODELS}
    assert all([row["week"] for row in report["rows"][model]] == [1, 2] for model in MODELS)
    assert report["models"][MODELS[2]]["markets"]["spread"]["count"] == 2
    assert set(report["rows"][MODELS[2]][0]["v3_probabilities"]) == {
        "football_probability", "market_probability", "blended_probability"}


def test_missing_and_incomplete_snapshots_are_explicit(tmp_path: Path):
    _write_week(tmp_path, 1)
    (tmp_path / "nfl/2025/week_01/team_stats.json").unlink()
    with pytest.raises(SnapshotError, match="Missing team_stats file"):
        evaluate(tmp_path, 2025, 1, 1, MODELS, NFLV3Config(), "DEVELOPMENT RESULT")


def test_exclusions_are_deterministic_and_machine_readable(tmp_path: Path):
    _write_week(tmp_path, 1, games=2, complete=False)
    report = evaluate(tmp_path, 2025, 1, 1, MODELS, NFLV3Config(), "DEVELOPMENT RESULT")
    assert report["eligibility"]["exclusion_reason_counts"] == {"incomplete_or_unmatched_outcome": 1}
    assert report["eligibility"]["exclusions"] == [{"week": 1, "game_id": "canonical-w1-0",
                                                       "reason": "incomplete_or_unmatched_outcome"}]


def test_zero_game_evaluation_raises_and_cli_writes_nothing(tmp_path: Path):
    _write_week(tmp_path, 1, complete=False)
    output = tmp_path / "result.json"
    with pytest.raises(ValueError, match="zero eligible games"):
        main(["--snapshot-root", str(tmp_path), "--season", "2025", "--development-end-week", "1",
              "--output", str(output)])
    assert not output.exists()


def test_model_universe_mismatch_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_week(tmp_path, 1)
    from backtesting import evaluate_nfl_v3 as module
    original = module.NFLGameMarketPredictor

    class MissingV3:
        def __init__(self, model, config=None):
            self.model = model
            self.inner = original(model, config)

        def project(self, *args):
            return None if self.model == MODELS[2] else self.inner.project(*args)

    monkeypatch.setattr(module, "NFLGameMarketPredictor", MissingV3)
    with pytest.raises(ValueError, match="different eligible game universes"):
        evaluate(tmp_path, 2025, 1, 1, MODELS, NFLV3Config(), "DEVELOPMENT RESULT")


def test_development_cli_rejects_holdout_overlap(tmp_path: Path):
    with pytest.raises(ValueError, match="research windows"):
        main(["--snapshot-root", str(tmp_path), "--season", "2025", "--development-end-week", "7",
              "--holdout-start-week", "7"])
