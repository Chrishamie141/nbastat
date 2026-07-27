import json
from argparse import Namespace
from pathlib import Path

from backtesting.build_snapshots import _manifest, main, request_plan
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
