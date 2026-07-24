import sqlite3

from models import DifficultyLevel, SportType
from nfl_parlay_builder import (
    build_nfl_parlay,
    calculate_edge_score,
    calculate_injury_adjustment,
    calculate_matchup_adjustment,
    calculate_player_consistency,
    calculate_weather_adjustment,
)
from prediction_storage import load_parlay_history, save_parlay_result


def test_build_nfl_safe_parlay_has_expected_shared_models():
    result = build_nfl_parlay("safe")

    assert result.parlay.sport == SportType.NFL
    assert result.parlay.difficulty == DifficultyLevel.SAFE
    assert 1 <= len(result.parlay.legs) <= 3
    assert all(leg.sport == SportType.NFL for leg in result.parlay.legs)
    assert all(leg.confidence >= 62 for leg in result.parlay.legs)


def test_build_nfl_safe_parlay_uses_non_empty_sample_fallback_without_live_keys(monkeypatch):
    for key in (
        "THE_ODDS_API_KEY",
        "ODDS_API_KEY",
        "SPORTSDATAIO_API_KEY",
        "SPORTS_DATA_IO_API_KEY",
        "OPENWEATHER_API_KEY",
        "OPEN_WEATHER_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    result = build_nfl_parlay("safe")

    assert 1 <= len(result.parlay.legs) <= 3
    assert all("sample" in leg.notes for leg in result.parlay.legs)
    assert "sample provider fallback" in result.notes


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


def test_nfl_edge_and_adjustment_helpers_score_expected_inputs():
    assert calculate_edge_score(72.5, 64.5) == 8.0
    steady_score = calculate_player_consistency([70, 72, 71, 73, 72])
    volatile_score = calculate_player_consistency([40, 95, 51, 104, 63])

    assert steady_score > volatile_score
    assert calculate_matchup_adjustment({"matchup_difficulty": "favorable"}) > 0
    assert calculate_weather_adjustment({"condition": "rain", "wind_mph": 18}, "PASS_YDS") < 0
    assert calculate_injury_adjustment([{"player": "Test Player", "status": "Questionable"}], "Test Player") < 0
