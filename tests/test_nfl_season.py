import json
from datetime import datetime, timedelta, timezone

from backtesting.nfl_season import (
    group_compatible_odds_requests, historical_quote_is_valid, plan_season,
    season_coverage, season_registry,
)


def game(gid, kickoff, week=1):
    return {"league": "nfl", "season": "2025", "week": week, "game_id": gid,
            "kickoff_time": kickoff, "home_team": "Home", "away_team": "Away",
            "venue": "Field", "status": "STATUS_FINAL", "source": "fixture"}


def test_grouping_only_combines_targets_inside_tolerance():
    games = [game("a", "2025-09-07T17:00:00Z"), game("b", "2025-09-07T17:05:00Z"),
             game("c", "2025-09-07T20:00:00Z")]
    assert len(group_compatible_odds_requests(games, tolerance_minutes=5)) == 2
    assert len(group_compatible_odds_requests(games, tolerance_minutes=0)) == 3


def test_grouped_quote_requires_identity_and_point_in_time_target():
    row = game("a", "2025-09-07T17:00:00Z")
    quote = {"game_id": "a", "captured_at": "2025-09-06T17:00:00Z"}
    assert historical_quote_is_valid(row, quote)
    assert not historical_quote_is_valid(row, {**quote, "game_id": "b"})
    assert not historical_quote_is_valid(row, {**quote, "captured_at": "2025-09-07T18:00:00Z"})


def test_season_plan_preserves_covered_week_and_exposes_partial_coverage(tmp_path):
    for week in (1, 2, 3):
        folder = tmp_path / f"nfl/2025/week_{week:02d}"
        folder.mkdir(parents=True)
        games = [game(str(week), f"2025-09-{week + 6:02d}T17:00:00Z", week)]
        (folder / "games.json").write_text(json.dumps(games))
        for name in ("weather", "injuries", "player_stats", "team_stats", "outcomes"):
            (folder / f"{name}.json").write_text("[]")
        (folder / "odds.json").write_text(json.dumps([{"game_id": str(week)}] if week < 3 else []))
    plan = plan_season(tmp_path, 2025, range(1, 4))
    assert [w["paid_requests"] for w in plan["weeks"]] == [0, 0, 1]
    assert plan["totals"]["naive_request_count"] == 1
    assert [g["game_id"] for g in season_registry(tmp_path, 2025, range(1, 4))] == ["1", "2", "3"]
    coverage = season_coverage(tmp_path, 2025, range(1, 4))
    assert coverage["status"] == "partial"
    assert coverage["weeks"][2]["games_without_odds"] == ["3"]
