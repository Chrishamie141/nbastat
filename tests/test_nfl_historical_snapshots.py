import json
from pathlib import Path

from backtesting.build_snapshots import _manifest
from backtesting.snapshot_coverage import coverage, markdown, write_coverage


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
