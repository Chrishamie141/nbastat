import json
from argparse import Namespace
from pathlib import Path

import pytest

from backtesting.build_snapshots import (
    _manifest, main, request_plan, prepare_canonical_games, validate_paid_odds_authorization,
)
from nfl_providers import JsonRawCache
from backtesting.snapshot_coverage import coverage, markdown, write_coverage
from backtesting.snapshot_sources import RawCache


def test_metadata_contains_cutoff_policy_and_source_lineage():
    normalized = {name: [] for name in ("games", "odds", "weather", "injuries", "player_stats", "team_stats", "outcomes")}
    normalized["games"] = [{"game_id": "g", "kickoff_time": "2024-09-01T17:00:00Z"}]
    normalized["odds"] = [{"game_id": "g", "captured_at": "2024-09-01T12:00:00Z"}]
    result = _manifest("nfl", "2024", 1, normalized, [], {"games": "espn", "odds": "archive"}, True)
    assert result["schema_version"] == 1
    assert result["normalization_version"] == "nfl-historical-v1"
    assert result["prediction_cutoffs"]["g"] == "2024-09-01T12:00:00Z"
    assert result["source_lineage"]["odds"]["provider"] == "archive"
    assert "strictly before" in result["cutoff_policy"]


def test_coverage_reason_codes_and_report_are_deterministic(tmp_path):
    week = tmp_path / "nfl/2024/week_01"; week.mkdir(parents=True)
    (week / "games.json").write_text(json.dumps([{"game_id": "real-provider-id",
        "kickoff_time": "2024-09-01T17:00:00Z", "home_team": "A", "away_team": "B"}]))
    for name in ("outcomes", "odds", "team_stats", "injuries", "weather", "player_stats"):
        (week / f"{name}.json").write_text("[]")
    first = coverage(tmp_path, requested_seasons=[2022, 2024]); second = coverage(tmp_path, requested_seasons=[2022, 2024])
    assert first == second
    assert first["missing_seasons"] == [2022]
    assert first["games_successfully_snapshotted"] == 0
    assert first["exclusion_reason_counts"] == {"MISSING_ODDS": 1, "MISSING_OUTCOME": 1, "MISSING_TEAM_HISTORY": 1}
    json_path, md_path = tmp_path / "report.json", tmp_path / "report.md"
    write_coverage(first, json_path, md_path)
    assert json.loads(json_path.read_text()) == first
    assert "Synthetic test fixtures are excluded" in md_path.read_text()


def test_default_real_data_tree_is_not_test_fixture_tree():
    repository = Path(__file__).resolve().parents[1]
    assert repository / "backtesting/data/nfl" != repository / "tests/fixtures/backtesting/nfl"
    assert "tests/fixtures" not in markdown(coverage(repository / "backtesting/data", requested_seasons=[2022]))


def test_request_plan_counts_only_games_without_cached_odds(tmp_path, capsys):
    week = tmp_path / "nfl/2025/week_02"; week.mkdir(parents=True)
    (week / "games.json").write_text(json.dumps([{"game_id": "a"}, {"game_id": "b"}]))
    (week / "odds.json").write_text(json.dumps([{"game_id": "a"}]))
    for name in ("team_stats", "outcomes"):
        (week / f"{name}.json").write_text("[]")
    args = Namespace(league="nfl", season="2025", start_week=2, end_week=2, data_dir=tmp_path)
    plan = request_plan(args)
    assert plan[0]["games_with_odds"] == plan[0]["expected_paid_requests"] == 1
    assert "Expected paid historical requests: 1" in capsys.readouterr().out


def test_paid_team_odds_authorization_requires_and_enforces_ceiling():
    plan = [{"paid_requests": 10}, {"paid_requests": 5}]
    with pytest.raises(Exception, match="required"):
        validate_paid_odds_authorization(plan, allow_paid=True, max_paid_requests=None)
    with pytest.raises(Exception, match="exceed authorized"):
        validate_paid_odds_authorization(plan, allow_paid=True, max_paid_requests=14)
    validate_paid_odds_authorization(plan, allow_paid=True, max_paid_requests=15)
    validate_paid_odds_authorization(plan, allow_paid=False, max_paid_requests=None)


def test_dry_run_constructs_no_provider_and_makes_no_network_calls(tmp_path, monkeypatch):
    destination = tmp_path / "snapshots"
    monkeypatch.setattr("backtesting.build_snapshots.create_sources", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network/provider constructed")))
    result = main(["--league", "nfl", "--season", "2025", "--start-week", "1", "--end-week", "4",
                   "--data-dir", str(destination), "--resume", "--dry-run"])
    assert result == 0 and not destination.exists()


def test_raw_cache_writes_auditable_checksum_metadata_and_reuses_response(tmp_path):
    calls = 0
    def fetch():
        nonlocal calls; calls += 1
        return [{"game_id": "g", "event_id": "e", "market": "h2h", "snapshot_timestamp": "2025-09-01T00:00:00Z"}]
    cache = RawCache(tmp_path)
    assert cache.get_or_fetch("odds-api", "nfl", "2025", 1, "odds", fetch) == cache.get_or_fetch("odds-api", "nfl", "2025", 1, "odds", fetch)
    meta = json.loads(cache.path("odds-api", "nfl", "2025", 1, "odds").with_suffix(".metadata.json").read_text())
    assert calls == 1 and cache.hits == cache.misses == 1
    assert len(meta["response_sha256"]) == 64 and meta["requested_historical_date"] == "2025-09-01T00:00:00Z"


def test_plan_prepares_schedule_without_constructing_odds_provider(tmp_path, monkeypatch):
    class ESPN:
        name = "espn"
        supported_datasets = {"games"}
        def fetch_games(self, league, season, week, week_range):
            return [{"game_id": "g", "kickoff_time": "2025-09-11T00:00:00Z",
                     "home_team": "A", "away_team": "B", "source": "espn"}]
    def sources(spec, *args):
        assert spec == "espn"
        return [ESPN()]
    monkeypatch.setattr("backtesting.build_snapshots.create_sources", sources)
    args = Namespace(league="nfl", season="2025", start_week=2, end_week=2,
                     data_dir=tmp_path, odds_hours_before_kickoff=24)
    prepare_canonical_games(args)
    assert json.loads((tmp_path / "nfl/2025/week_02/games.json").read_text())[0]["game_id"] == "g"


def test_exact_plan_is_cache_aware_and_credits_differ_from_http_requests(tmp_path):
    week = tmp_path / "nfl/2025/week_02"; week.mkdir(parents=True)
    games = [{"game_id": "a", "kickoff_time": "2025-09-11T00:00:00Z"},
             {"game_id": "b", "kickoff_time": "2025-09-12T00:00:00Z"}]
    (week / "games.json").write_text(json.dumps(games))
    for dataset in ("odds", "team_stats", "outcomes"):
        (week / f"{dataset}.json").write_text("[]")
    params = {"regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "american",
              "date": "2025-09-10T00:00:00Z"}
    cache = JsonRawCache(tmp_path.parent / "raw_cache")
    path = cache.path("odds-api", "nfl", "2025", 2, "odds", params)
    path.parent.mkdir(parents=True); path.write_text("[]")
    args = Namespace(league="nfl", season="2025", start_week=2, end_week=2,
                     data_dir=tmp_path, odds_hours_before_kickoff=24)
    row = request_plan(args)[0]
    assert row["historical_requests_needed"] == 2
    assert row["cache_hits"] == 1 and row["paid_requests"] == 1
    assert row["estimated_credits"] == 30


def test_cache_identity_is_parameter_complete_and_has_no_api_key(tmp_path):
    cache = JsonRawCache(tmp_path)
    base = {"date": "2025-09-01T00:00:00Z", "regions": "us", "markets": "h2h",
            "oddsFormat": "american", "apiKey": "secret"}
    identity = cache.identity("odds-api", "nfl", 2025, 1, "odds", base)
    assert "apiKey" not in identity["params"] and "secret" not in json.dumps(identity)
    assert cache.path("odds-api", "nfl", 2025, 1, "odds", base) != cache.path(
        "odds-api", "nfl", 2025, 1, "odds", {**base, "markets": "spreads"})


def test_paid_execution_requires_explicit_authorization(tmp_path, monkeypatch):
    week = tmp_path / "nfl/2025/week_02"; week.mkdir(parents=True)
    (week / "games.json").write_text(json.dumps([{"game_id": "g", "kickoff_time": "2025-09-11T00:00:00Z"}]))
    for dataset in ("odds", "team_stats", "outcomes"):
        (week / f"{dataset}.json").write_text("[]")
    monkeypatch.setattr("backtesting.build_snapshots.create_sources", lambda *a, **k: (_ for _ in ()).throw(AssertionError("constructed")))
    assert main(["--league", "nfl", "--season", "2025", "--start-week", "2", "--end-week", "2",
                 "--data-dir", str(tmp_path), "--resume"]) == 2
