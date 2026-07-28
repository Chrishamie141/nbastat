import json
from datetime import datetime, timedelta, timezone

from backtesting.nfl_season import (
    execute_grouped_odds, group_compatible_odds_requests, historical_quote_is_valid, plan_season,
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


class FixtureOddsProvider:
    def __init__(self, partial=False):
        self.partial = partial
        self.calls = []
        self.last_diagnostics = {}

    def fetch_odds(self, season, week, games, snapshot_time=None):
        self.calls.append(([g["game_id"] for g in games], snapshot_time))
        selected = games[:1] if self.partial and len(games) > 1 else games
        self.last_diagnostics = {"provider_events_received": len(selected),
            "provider_events_matched": len(selected), "provider_events_discarded": 0}
        return [{"game_id": g["game_id"], "event_id": "provider-" + g["game_id"],
            "market": "h2h", "selection": g["home_team"], "line": 0, "odds": -110,
            "sportsbook": "Fixture", "bookmaker": "fixture", "captured_at": snapshot_time,
            "snapshot_timestamp": snapshot_time, "data_as_of": snapshot_time,
            "provider_event_matched": True, "is_pregame": True,
            "source": "the-odds-api-historical"} for g in selected]


def test_grouped_execution_matches_individual_and_falls_back_atomically(tmp_path):
    root = tmp_path / "snapshots"
    folder = root / "nfl/2025/week_03"
    folder.mkdir(parents=True)
    games = [game("a", "2025-09-21T17:00:00Z", 3),
             {**game("b", "2025-09-21T17:00:00Z", 3), "home_team": "CHI", "away_team": "GB"}]
    (folder / "games.json").write_text(json.dumps(games))
    (folder / "odds.json").write_text("[]")
    provider = FixtureOddsProvider(partial=True)
    diagnostics = execute_grouped_odds(root, 2025, [3], provider=provider)
    rows = json.loads((folder / "odds.json").read_text())
    assert {r["game_id"] for r in rows} == {"a", "b"}
    assert len(provider.calls) == 2  # one group, one explicit safety fallback
    assert diagnostics["games_requiring_fallback"] == ["b"]
    assert diagnostics["games_incomplete"] == []
    # Resume treats both persisted games as authoritative and makes no calls.
    resumed = FixtureOddsProvider()
    execute_grouped_odds(root, 2025, [3], provider=resumed)
    assert resumed.calls == []


def test_plan_uses_group_cache_identity_and_never_counts_existing_odds(tmp_path):
    root = tmp_path / "snapshots"
    folder = root / "nfl/2025/week_03"
    folder.mkdir(parents=True)
    games = [game("a", "2025-09-21T17:00:00Z", 3), game("b", "2025-09-21T17:00:00Z", 3)]
    (folder / "games.json").write_text(json.dumps(games))
    (folder / "odds.json").write_text("[]")
    plan = plan_season(root, 2025, [3])
    assert plan["totals"]["naive_request_count"] == 2
    assert plan["totals"]["planned_grouped_requests"] == 1
    assert plan["totals"]["paid_requests"] == 1
    (folder / "odds.json").write_text(json.dumps([{"game_id": "a"}]))
    assert plan_season(root, 2025, [3])["totals"]["naive_request_count"] == 1
