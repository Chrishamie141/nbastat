import os
import sqlite3

import pytest

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


def _force_sample_nfl_providers(monkeypatch):
    import nfl_parlay_builder as builder
    from nfl_data_service import NFL_SAMPLE_GAMES, NFL_SAMPLE_INJURIES, NFL_SAMPLE_LINES, NFL_SAMPLE_RECENT_STATS, NFL_SAMPLE_WEATHER

    monkeypatch.setattr(builder, "get_nfl_games", lambda: list(NFL_SAMPLE_GAMES))
    monkeypatch.setattr(builder, "get_nfl_player_props", lambda team=None: dict(NFL_SAMPLE_LINES))
    monkeypatch.setattr(builder, "get_nfl_team_lines", lambda team=None: [])
    monkeypatch.setattr(builder, "get_nfl_player_recent_stats", lambda team=None: dict(NFL_SAMPLE_RECENT_STATS))
    monkeypatch.setattr(builder, "get_nfl_injuries", lambda team=None: list(NFL_SAMPLE_INJURIES))
    monkeypatch.setattr(builder, "get_nfl_weather", lambda game=None: dict(NFL_SAMPLE_WEATHER))


def test_build_nfl_safe_parlay_has_expected_shared_models(monkeypatch):
    _force_sample_nfl_providers(monkeypatch)

    result = build_nfl_parlay("safe")

    assert result.parlay.sport == SportType.NFL
    assert result.parlay.difficulty == DifficultyLevel.SAFE
    assert 1 <= len(result.parlay.legs) <= 3
    assert all(leg.sport == SportType.NFL for leg in result.parlay.legs)
    assert all(leg.confidence >= 62 for leg in result.parlay.legs)


def test_build_nfl_safe_parlay_uses_non_empty_sample_fallback_without_live_keys(monkeypatch):
    _force_sample_nfl_providers(monkeypatch)

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


def test_safe_sample_fallback_survives_unusable_espn_and_optional_provider_gaps(monkeypatch):
    import nfl_parlay_builder as builder
    from nfl_data_service import NFL_SAMPLE_GAMES, NFL_SAMPLE_LINES, NFL_SAMPLE_WEATHER

    monkeypatch.setattr(builder, "get_nfl_games", lambda: list(NFL_SAMPLE_GAMES))
    monkeypatch.setattr(builder, "get_nfl_player_props", lambda team=None: dict(NFL_SAMPLE_LINES))
    monkeypatch.setattr(builder, "get_nfl_team_lines", lambda team=None: [])
    monkeypatch.setattr(
        builder,
        "get_nfl_player_recent_stats",
        lambda team=None: {"Unrelated ESPN Player": {"team": "DAL", "passing_yards": [99]}},
    )
    monkeypatch.setattr(builder, "get_nfl_injuries", lambda team=None: [])
    monkeypatch.setattr(builder, "get_nfl_weather", lambda game=None: dict(NFL_SAMPLE_WEATHER))

    first = build_nfl_parlay("safe")
    second = build_nfl_parlay("safe")

    assert 1 <= len(first.parlay.legs) <= 3
    assert [leg.prediction for leg in first.parlay.legs] == [leg.prediction for leg in second.parlay.legs]
    assert [leg.confidence for leg in first.parlay.legs] == [leg.confidence for leg in second.parlay.legs]
    assert all("Sample/offline fallback data" in leg.notes for leg in first.parlay.legs)


def test_safe_fallback_without_live_keys_or_optional_weather_and_injury(monkeypatch):
    for key in (
        "THE_ODDS_API_KEY",
        "ODDS_API_KEY",
        "OPENWEATHER_API_KEY",
        "OPEN_WEATHER_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    result = build_nfl_parlay("safe")

    assert 1 <= len(result.parlay.legs) <= 3
    assert all("sample" in leg.notes.lower() and leg.confidence >= 62 for leg in result.parlay.legs)


def test_safe_fallback_with_complete_live_provider_failure(monkeypatch):
    import nfl_parlay_builder as builder
    from nfl_data_service import NFL_SAMPLE_LINES

    monkeypatch.setattr(builder, "get_nfl_games", lambda: [])
    monkeypatch.setattr(builder, "get_nfl_player_props", lambda team=None: dict(NFL_SAMPLE_LINES))
    monkeypatch.setattr(builder, "get_nfl_team_lines", lambda team=None: [])
    monkeypatch.setattr(builder, "get_nfl_player_recent_stats", lambda team=None: {})
    monkeypatch.setattr(builder, "get_nfl_injuries", lambda team=None: [])
    monkeypatch.setattr(builder, "get_nfl_weather", lambda game=None: {})

    result = build_nfl_parlay("safe")

    assert 1 <= len(result.parlay.legs) <= 3
    assert all("Sample/offline fallback data" in leg.notes for leg in result.parlay.legs)


def test_safe_fallback_with_no_recent_rows_no_weather_key_and_optional_injuries(monkeypatch):
    import nfl_parlay_builder as builder
    from nfl_data_service import NFL_SAMPLE_GAMES, NFL_SAMPLE_LINES

    monkeypatch.setattr(builder, "get_nfl_games", lambda: list(NFL_SAMPLE_GAMES))
    monkeypatch.setattr(builder, "get_nfl_player_props", lambda team=None: dict(NFL_SAMPLE_LINES))
    monkeypatch.setattr(builder, "get_nfl_team_lines", lambda team=None: [])
    monkeypatch.setattr(builder, "get_nfl_player_recent_stats", lambda team=None: {})
    monkeypatch.setattr(builder, "get_nfl_injuries", lambda team=None: [])
    monkeypatch.setattr(builder, "get_nfl_weather", lambda game=None: {})

    result = build_nfl_parlay("safe")

    assert 1 <= len(result.parlay.legs) <= 3
    assert all(leg.confidence >= 62 for leg in result.parlay.legs)


def test_safe_fallback_resolves_player_name_and_stat_type_alias_mismatch(monkeypatch):
    import nfl_parlay_builder as builder
    from nfl_data_service import NFL_SAMPLE_GAMES, NFL_SAMPLE_WEATHER

    props = {"Amon Ra St Brown": {"receiving_yards": [{"line": 69.5, "odds": -110, "provider": "sample"}]}}
    monkeypatch.setattr(builder, "get_nfl_games", lambda: list(NFL_SAMPLE_GAMES))
    monkeypatch.setattr(builder, "get_nfl_player_props", lambda team=None: props)
    monkeypatch.setattr(builder, "get_nfl_team_lines", lambda team=None: [])
    monkeypatch.setattr(builder, "get_nfl_player_recent_stats", lambda team=None: {"Amon-Ra St. Brown": {"team": "DET", "ReceivingYards": [76, 94, 71, 83, 65]}})
    monkeypatch.setattr(builder, "get_nfl_injuries", lambda team=None: [])
    monkeypatch.setattr(builder, "get_nfl_weather", lambda game=None: dict(NFL_SAMPLE_WEATHER))

    result = build_nfl_parlay("safe")

    assert len(result.parlay.legs) == 1
    assert result.parlay.legs[0].stat_type == "REC_YDS"
    assert result.parlay.legs[0].confidence >= 62


def test_safe_fallback_replaces_unusable_target_stat_values_but_preserves_zero(monkeypatch):
    import nfl_parlay_builder as builder
    from nfl_data_service import NFL_SAMPLE_GAMES, NFL_SAMPLE_WEATHER

    props = {"Christian McCaffrey": {"TD": [{"line": [None], "odds": "-125", "provider": "sample"}]}}
    bad_stats = {"Christian McCaffrey": {"team": "SF", "TD": [None, [], [None], "", float("nan"), 0]}}
    monkeypatch.setattr(builder, "get_nfl_games", lambda: list(NFL_SAMPLE_GAMES))
    monkeypatch.setattr(builder, "get_nfl_player_props", lambda team=None: props)
    monkeypatch.setattr(builder, "get_nfl_team_lines", lambda team=None: [])
    monkeypatch.setattr(builder, "get_nfl_player_recent_stats", lambda team=None: bad_stats)
    monkeypatch.setattr(builder, "get_nfl_injuries", lambda team=None: [])
    monkeypatch.setattr(builder, "get_nfl_weather", lambda game=None: dict(NFL_SAMPLE_WEATHER))

    result = build_nfl_parlay("safe")

    assert len(result.parlay.legs) == 1
    assert result.parlay.legs[0].line == 0.5
    assert result.parlay.legs[0].confidence >= 62


def test_valid_live_stats_are_not_overwritten_by_sample_backfill(monkeypatch):
    import nfl_parlay_builder as builder
    from nfl_data_service import NFL_SAMPLE_GAMES, NFL_SAMPLE_WEATHER

    props = {"Josh Allen": {"PASS_YDS": [{"line": 239.5, "odds": -110, "provider": "sample"}]}}
    live_stats = {"Josh Allen": {"team": "BUF", "PASS_YDS": [400, 401, 402, 403, 404]}}
    monkeypatch.setattr(builder, "get_nfl_games", lambda: list(NFL_SAMPLE_GAMES))
    monkeypatch.setattr(builder, "get_nfl_player_props", lambda team=None: props)
    monkeypatch.setattr(builder, "get_nfl_team_lines", lambda team=None: [])
    monkeypatch.setattr(builder, "get_nfl_player_recent_stats", lambda team=None: live_stats)
    monkeypatch.setattr(builder, "get_nfl_injuries", lambda team=None: [])
    monkeypatch.setattr(builder, "get_nfl_weather", lambda game=None: dict(NFL_SAMPLE_WEATHER))

    result = build_nfl_parlay("safe")

    assert len(result.parlay.legs) == 1
    assert result.parlay.legs[0].prediction.startswith("Josh Allen over 239.5")
    assert result.parlay.legs[0].confidence >= 62


@pytest.mark.skipif(os.getenv("SMARTBETS_RUN_LIVE_NFL_TESTS") != "1", reason="live-first NFL provider path is opt-in")
def test_build_nfl_safe_parlay_live_first_path_smoke():
    result = build_nfl_parlay("safe")

    assert result.parlay.sport == SportType.NFL
    assert result.parlay.difficulty == DifficultyLevel.SAFE
    assert len(result.parlay.legs) <= 3
