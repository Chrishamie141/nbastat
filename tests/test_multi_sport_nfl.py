import sqlite3

from models import DifficultyLevel, SportType
from nfl_parlay_builder import build_nfl_parlay
from prediction_storage import load_parlay_history, save_parlay_result


def test_build_nfl_safe_parlay_has_expected_shared_models():
    result = build_nfl_parlay("safe")

    assert result.parlay.sport == SportType.NFL
    assert result.parlay.difficulty == DifficultyLevel.SAFE
    assert 1 <= len(result.parlay.legs) <= 3
    assert all(leg.sport == SportType.NFL for leg in result.parlay.legs)
    assert all(leg.confidence >= 62 for leg in result.parlay.legs)


def test_save_and_filter_parlay_history(tmp_path):
    db_file = tmp_path / "predictions.db"
    result = build_nfl_parlay("balanced")

    parlay_id = save_parlay_result(result, db_file=db_file)
    rows = load_parlay_history(sport="NFL", difficulty="BALANCED", result_status="pending", db_file=db_file)

    assert parlay_id == 1
    assert len(rows) == 1
    assert rows[0]["sport"] == "NFL"
    assert rows[0]["difficulty"] == "BALANCED"
    assert rows[0]["result_status"] == "pending"

    with sqlite3.connect(db_file) as conn:
        count = conn.execute("SELECT COUNT(*) FROM parlay_history").fetchone()[0]
    assert count == 1
