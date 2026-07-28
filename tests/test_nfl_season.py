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
    quote = {"game_id": "a", "snapshot_timestamp": "2025-09-06T17:00:00Z", "captured_at": "2025-09-06T17:00:00Z", "market": "h2h", "selection": "Home", "line": 0, "odds": -110, "sportsbook": "Book"}
    assert historical_quote_is_valid(row, quote)
    assert not historical_quote_is_valid(row, {**quote, "game_id": "b"})
    assert not historical_quote_is_valid(row, {**quote, "snapshot_timestamp": "2025-09-07T18:00:00Z"})


def test_season_plan_preserves_covered_week_and_exposes_partial_coverage(tmp_path):
    for week in (1, 2, 3):
        folder = tmp_path / f"nfl/2025/week_{week:02d}"
        folder.mkdir(parents=True)
        games = [game(str(week), f"2025-09-{week + 6:02d}T17:00:00Z", week)]
        (folder / "games.json").write_text(json.dumps(games))
        for name in ("weather", "injuries", "player_stats", "team_stats", "outcomes"):
            (folder / f"{name}.json").write_text("[]")
        (folder / "odds.json").write_text(json.dumps([{**{"game_id": str(week), "market": "h2h", "selection": "Home", "line": 0, "odds": -110, "sportsbook": "Book"}, "snapshot_timestamp": f"2025-09-{week + 5:02d}T17:00:00Z", "captured_at": f"2025-09-{week + 5:02d}T17:00:00Z", "data_as_of": f"2025-09-{week + 5:02d}T17:00:00Z"}] if week < 3 else []))
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
    assert plan_season(root, 2025, [3])["totals"]["naive_request_count"] == 2


def cached_payload(timestamp, *, market_update=None):
    return {"timestamp": timestamp, "data": [{
        "id": "event-a", "commence_time": "2025-09-21T17:00:00Z",
        "home_team": "Home", "away_team": "Away", "bookmakers": [{
            "key": "book", "title": "Book", "markets": [{"key": "h2h",
            "last_update": market_update or "2025-09-20T16:57:00Z",
            "outcomes": [{"name": "Home", "price": -110},
                         {"name": "Away", "price": -110}]}]}]}]}


def write_group_cache(root, games, payload):
    from nfl_providers import JsonRawCache
    from backtesting.nfl_season import _request_params
    target = group_compatible_odds_requests(games, tolerance_minutes=5)[0]["timestamp"]
    cache = JsonRawCache(root.parent / "raw_cache")
    path = cache.path("odds-api", "nfl", 2025, 3, "odds", _request_params(target))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def test_real_historical_timestamp_shape_and_leakage_guards():
    row = game("a", "2025-09-21T17:00:00Z", 3)
    base = {"game_id": "a", "market": "h2h", "selection": "Home", "line": 0,
            "odds": -110, "sportsbook": "Book", "snapshot_timestamp": "2025-09-20T16:58:00Z",
            "captured_at": "2025-09-20T16:58:00Z", "data_as_of": "2025-09-20T16:58:00Z",
            "market_last_update": "2025-09-20T16:57:00Z"}
    assert historical_quote_is_valid(row, base, tolerance_minutes=5)
    assert not historical_quote_is_valid(row, {**base, "snapshot_timestamp": "2025-09-21T17:01:00Z"}, tolerance_minutes=2000)
    assert not historical_quote_is_valid(row, {**base, "snapshot_timestamp": "2025-09-20T16:54:00Z"}, tolerance_minutes=5)
    assert not historical_quote_is_valid(row, {**base, "market_last_update": "2025-09-20T16:59:00Z"}, tolerance_minutes=5)


def test_validated_group_cache_is_reused_without_network(tmp_path):
    root = tmp_path / "snapshots"
    folder = root / "nfl/2025/week_03"; folder.mkdir(parents=True)
    games = [game("a", "2025-09-21T17:00:00Z", 3)]
    (folder / "games.json").write_text(json.dumps(games)); (folder / "odds.json").write_text("[]")
    write_group_cache(root, games, cached_payload("2025-09-20T16:58:00Z"))
    plan = plan_season(root, 2025, [3])
    assert plan["totals"]["raw_cache_hits"] == 1
    assert plan["totals"]["validated_cache_hits"] == 1
    assert plan["totals"]["invalid_cache_hits"] == 0
    assert plan["totals"]["paid_requests"] == 0
    diagnostics = execute_grouped_odds(root, 2025, [3])
    assert diagnostics["grouped_cache_hits"] == 1
    assert diagnostics["games_incomplete"] == []
    assert json.loads((folder / "odds.json").read_text())[0]["captured_at"] == "2025-09-20T16:58:00Z"


def test_raw_invalid_cache_does_not_satisfy_or_hide_paid_work(tmp_path):
    root = tmp_path / "snapshots"
    folder = root / "nfl/2025/week_03"; folder.mkdir(parents=True)
    games = [game("a", "2025-09-21T17:00:00Z", 3)]
    (folder / "games.json").write_text(json.dumps(games)); (folder / "odds.json").write_text("[]")
    write_group_cache(root, games, cached_payload("2025-09-20T16:58:00Z", market_update="2025-09-20T16:59:00Z"))
    plan = plan_season(root, 2025, [3])
    assert plan["totals"]["raw_cache_hits"] == 1
    assert plan["totals"]["validated_cache_hits"] == 0
    assert plan["totals"]["invalid_cache_hits"] == 1
    assert plan["totals"]["games_with_usable_cached_odds"] == 0
    assert plan["totals"]["paid_requests"] == 1
    assert plan["weeks"][0]["odds_status"] == "missing"


def test_malformed_cache_is_explicit_and_plans_replacement(tmp_path):
    root = tmp_path / "snapshots"; folder = root / "nfl/2025/week_03"; folder.mkdir(parents=True)
    games = [game("a", "2025-09-21T17:00:00Z", 3)]
    (folder / "games.json").write_text(json.dumps(games)); (folder / "odds.json").write_text("[]")
    from nfl_providers import JsonRawCache
    from backtesting.nfl_season import _request_params
    target = group_compatible_odds_requests(games, tolerance_minutes=5)[0]["timestamp"]
    path = JsonRawCache(root.parent / "raw_cache").path("odds-api", "nfl", 2025, 3, "odds", _request_params(target))
    path.parent.mkdir(parents=True); path.write_text("{not json")
    plan = plan_season(root, 2025, [3])
    assert plan["requests"][0]["cache_state"] == "malformed"
    assert plan["totals"]["total_paid_request_budget"] == 1


def test_cache_overwrite_quarantines_invalid_response_and_fetches(tmp_path):
    from nfl_providers import JsonRawCache
    cache = JsonRawCache(tmp_path)
    args = ("odds-api", "nfl", 2025, 3, "odds", {"date": "target"})
    path = cache.path(*args); path.parent.mkdir(parents=True); path.write_text("{bad")
    calls = []
    value = cache.get_or_fetch(*args, lambda: calls.append(1) or {"data": []},
                               overwrite=True, replacement_reason="malformed_json")
    assert value == {"data": []} and calls == [1]
    assert list(path.parent.glob("*.invalid-*.json"))
    meta = json.loads(path.with_suffix(".metadata.json").read_text())
    assert meta["previous_cache_invalidated"] and meta["replacement_fetched"]
    assert meta["replacement_reason"] == "malformed_json"


def test_partial_group_accounts_for_two_individual_fallbacks(tmp_path):
    root = tmp_path / "snapshots"; folder = root / "nfl/2025/week_03"; folder.mkdir(parents=True)
    games = [game("a", "2025-09-21T17:00:00Z", 3),
             {**game("b", "2025-09-21T17:00:00Z", 3), "home_team": "B", "away_team": "BB"},
             {**game("c", "2025-09-21T17:00:00Z", 3), "home_team": "C", "away_team": "CC"}]
    (folder / "games.json").write_text(json.dumps(games)); (folder / "odds.json").write_text("[]")
    write_group_cache(root, games, cached_payload("2025-09-20T16:58:00Z"))
    plan = plan_season(root, 2025, [3])
    assert plan["requests"][0]["validated_games"] == ["a"]
    assert plan["requests"][0]["invalid_games"] == ["b", "c"]
    assert plan["totals"]["grouped_paid_requests"] == 0
    assert plan["totals"]["individual_fallback_paid_requests"] == 2
    assert plan["totals"]["paid_requests"] == 2


def test_paid_budget_guard_stops_before_unreviewed_fallback(tmp_path):
    from backtesting.nfl_season import PaidRequestBudgetExceeded
    root = tmp_path / "snapshots"; folder = root / "nfl/2025/week_03"; folder.mkdir(parents=True)
    games = [game("a", "2025-09-21T17:00:00Z", 3), game("b", "2025-09-21T17:00:00Z", 3)]
    (folder / "games.json").write_text(json.dumps(games)); (folder / "odds.json").write_text("[]")
    provider = FixtureOddsProvider(partial=True)
    import pytest
    with pytest.raises(PaidRequestBudgetExceeded) as error:
        execute_grouped_odds(root, 2025, [3], provider=provider, paid_request_budget=1)
    assert error.value.additional_paid_requests_required == 1
    assert len(provider.calls) == 1


def test_audit_mode_skips_preparation_and_does_not_mutate(tmp_path, monkeypatch):
    import backtesting.build_nfl_season as command
    root = tmp_path / "snapshots"; root.mkdir()
    before = list(tmp_path.rglob("*"))
    monkeypatch.setattr(command, "build_snapshots", lambda *_: (_ for _ in ()).throw(AssertionError("provider path")))
    assert command.main(["--season", "2025", "--start-week", "3", "--end-week", "3",
                         "--data-dir", str(root), "--audit-odds-cache"]) == 0
    assert list(tmp_path.rglob("*")) == before
