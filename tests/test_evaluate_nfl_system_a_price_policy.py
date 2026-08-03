from __future__ import annotations

import csv
from pathlib import Path

import pytest

from backtesting.evaluate_nfl_system_a_price_policy import join_calibrated_prices


def _calibration() -> list[dict]:
    return [{
        "season": 2025, "week": 3, "game_id": "g1", "canonical_player_id": "p1",
        "market": "receptions", "line": 4.5, "side": side,
        "result": "WIN" if side == "OVER" else "LOSS", "calibration_ready": True,
        "calibrated_probability": .72 if side == "OVER" else .31,
        "raw_probability": .70 if side == "OVER" else .30,
        "calibration_method": "shrink_75", "calibration_training_rows": 500,
    } for side in ("OVER", "UNDER")]


def _prices(path: Path, *, conflict: bool = False) -> None:
    fields = ["season", "week", "game_id", "canonical_player_id", "market", "line", "side",
              "bookmaker", "american_odds", "decimal_odds", "no_vig_market_probability",
              "model_probability", "grade", "profit_units", "outcome", "quote_timestamp"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for side in ("OVER", "UNDER"):
            win = side == "OVER"
            writer.writerow({"season": 2025, "week": 3, "game_id": "g1",
                "canonical_player_id": "p1", "market": "receptions", "line": 4.5, "side": side,
                "bookmaker": "book", "american_odds": -110, "decimal_odds": 1.9090909,
                "no_vig_market_probability": .5, "model_probability": .6 if win else .4,
                "grade": "LOSS" if conflict and win else "WIN" if win else "LOSS",
                "profit_units": .9090909 if win else -1, "outcome": 5,
                "quote_timestamp": "2025-09-01T00:00:00Z"})


def test_join_normalizes_both_sides_before_price_decision(tmp_path: Path) -> None:
    path = tmp_path / "prices.csv"; _prices(path)
    rows, audit = join_calibrated_prices(_calibration(), [path])
    assert len(rows) == 2
    assert sum(row["model_probability"] for row in rows) == pytest.approx(1.0)
    assert next(row for row in rows if row["side"] == "OVER")["model_probability"] == pytest.approx(.72 / 1.03)
    assert audit["joined_complete_bases"] == 1


def test_join_rejects_outcome_contradictions(tmp_path: Path) -> None:
    path = tmp_path / "prices.csv"; _prices(path, conflict=True)
    with pytest.raises(ValueError, match="outcome contradictions"):
        join_calibrated_prices(_calibration(), [path])


def test_incomplete_price_pair_is_excluded_not_partially_bet(tmp_path: Path) -> None:
    path = tmp_path / "prices.csv"; _prices(path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))[:1]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0]); writer.writeheader(); writer.writerows(rows)
    joined, audit = join_calibrated_prices(_calibration(), [path])
    assert joined == []
    assert audit["joined_complete_bases"] == 0
