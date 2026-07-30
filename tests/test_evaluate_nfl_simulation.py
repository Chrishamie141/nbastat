from __future__ import annotations

from backtesting.nfl_simulation import NFLGameSimulator
from backtesting.historical_provider import HistoricalSnapshotProvider
from backtesting.nfl_game_predictor import NFLGameMarketPredictorV2
from backtesting.snapshots import SnapshotError, normalize_dataset, snapshot_week_dir
from backtesting.team_history import filter_game_history, filter_market_quotes, prediction_cutoff


def game(game_id="target", kickoff="2025-09-08T20:20:00Z"):
    return {"game_id": game_id, "season": 2025, "week": 2, "home_team": "BUF",
            "away_team": "MIA", "kickoff_time": kickoff,
            "prediction_cutoff": "2025-09-07T20:20:00Z",
            "projected_home_points": 24, "projected_away_points": 21}


def player(gid, stamp, *, season=2025, week=1, team="BUF"):
    return {"game_id": gid, "season": season, "week": week, "team": team,
            "player_id": "qb", "player_name": "Quarterback", "position": "QB",
            "completed_at": stamp, "data_as_of": stamp, "passing_yards": 250}


def team(gid, stamp, *, season=2025, week=1, name="BUF"):
    return {"game_id": gid, "season": season, "week": week, "team": name,
            "opponent": "NYJ", "completed_at": stamp, "data_as_of": stamp,
            "record_role": "completed_game_history", "is_pregame": False,
            "points_for": 24, "points_against": 20}


def test_player_history_is_filtered_per_target_by_strict_timestamp_not_week():
    rows = [
        player("target", "2025-09-08T23:30:00Z", week=2),       # target postgame
        player("week-3", "2025-09-15T23:30:00Z", week=3),      # future week
        player("later", "2025-09-08T03:30:00Z", week=2),       # later than cutoff
        player("early-sunday", "2025-09-07T23:30:00Z", week=2),# kicked earlier, known too late
        player("monday-prior", "2025-09-07T19:00:00Z", week=2),# same week but known in time
        player("prior-season", "2024-12-20T23:00:00Z", season=2024, week=16),
    ]
    result = filter_game_history(game(), rows, dataset="player")
    assert [r["game_id"] for r in result.rows] == ["monday-prior", "prior-season"]
    assert result.rejected_future == 4
    assert result.loaded == 6
    assert result.latest_timestamp == "2025-09-07T19:00:00Z"


def test_team_history_has_equivalent_chronology_and_role_rules():
    rows = [team("old", "2025-09-01T00:00:00Z"),
            team("future", "2025-09-07T21:00:00Z", week=2),
            team("target", "2025-09-08T23:00:00Z", week=2),
            {**team("bad-role", "2025-09-01T00:00:00Z"), "record_role": "outcome"},
            team("other", "2025-09-01T00:00:00Z", name="DAL")]
    result = filter_game_history(game(), rows, dataset="team")
    assert [r["game_id"] for r in result.rows] == ["old"]
    assert result.rejected_future == 2
    assert result.rejected_other == 2


def test_unknown_or_malformed_timestamps_cannot_prove_eligibility():
    rows = [player("missing", None), player("malformed", "not-a-time")]
    # Remove the explicit None-valued fields to exercise a truly absent timestamp too.
    rows[0].pop("completed_at"); rows[0].pop("data_as_of")
    result = filter_game_history(game(), rows, dataset="player")
    assert result.rows == []
    assert result.rejected_unknown_timestamp == 2
    assert {r["rejection_reason"] for r in result.rejected_rows} == {"unknown_timestamp"}


def test_explicit_prediction_cutoff_is_authoritative_and_simulator_remains_defensive():
    target = game()
    assert prediction_cutoff(target).isoformat() == "2025-09-07T20:20:00+00:00"
    future = player("other", "2025-09-07T21:00:00Z")
    try:
        NFLGameSimulator().simulate(target, [], [future], None, "v3", 5, 1)
    except ValueError as exc:
        assert "future history" in str(exc)
    else:
        raise AssertionError("simulator accepted a future row")


def test_filtering_before_simulation_produces_diagnostics_and_team_result():
    target = game()
    filtered = filter_game_history(target, [player("old", "2025-09-01T00:00:00Z"),
                                             player("future", "2025-09-08T00:00:00Z")],
                                   dataset="player")
    result = NFLGameSimulator().simulate(target, [], filtered.rows, None, "v3", 20, 141)
    assert len(result.home_points) == 20
    assert filtered.loaded == 2 and len(filtered.rows) == 1 and filtered.rejected_future == 1


def test_cutoff_rejects_every_observation_after_explicit_prediction_time():
    target = {**game(kickoff="2025-09-07T13:00:00Z"),
              "prediction_cutoff": "2025-09-06T13:00:00Z"}
    rows = [team("accepted", "2025-09-06T12:00:00Z"),
            team("saturday-late", "2025-09-06T14:00:00Z"),
            team("sunday", "2025-09-07T10:00:00Z")]
    result = filter_game_history(target, rows, dataset="team")
    assert [row["game_id"] for row in result.rows] == ["accepted"]
    assert result.rejected_future == 2


def test_snapshot_preserves_prediction_timestamp_and_player_history_is_optional(tmp_path):
    normalized = normalize_dataset("games", [{**game(), "prediction_timestamp": "2025-09-07T12:20:00-08:00",
                                                "prediction_cutoff": None}], "nfl", "2025", 2)
    assert normalized[0]["prediction_timestamp"] == "2025-09-07T20:20:00Z"
    directory = snapshot_week_dir(tmp_path, "nfl", 2025, 2)
    directory.mkdir(parents=True)
    (directory / "games.json").write_text(__import__("json").dumps(normalized))
    (directory / "team_stats.json").write_text("[]")
    provider = HistoricalSnapshotProvider(tmp_path)
    loaded = provider.get_games("nfl", "2025", 2)[0]
    assert loaded["prediction_timestamp"] == "2025-09-07T20:20:00Z"
    views = provider.get_game_histories("nfl", "2025", 2, loaded)
    assert views.player_history.rows == [] and views.player_history.loaded == 0


def test_prediction_boundary_normalization_rules_and_precedence():
    base = {**game(), "prediction_cutoff": "2025-09-08T20:20:00Z"}
    assert normalize_dataset("games", [base], "nfl", "2025", 2)[0]["prediction_cutoff"] == base["kickoff_time"]
    both = {**game(), "prediction_cutoff": "2025-09-07T10:00:00Z",
            "prediction_timestamp": "2025-09-07T12:00:00Z"}
    assert prediction_cutoff(both).isoformat() == "2025-09-07T10:00:00+00:00"
    for field, value, code in (
        ("prediction_cutoff", "bad", "invalid_prediction_cutoff"),
        ("prediction_cutoff", "2025-09-09T00:00:00Z", "prediction_cutoff_after_kickoff"),
        ("prediction_timestamp", "2025-09-09T00:00:00Z", "prediction_timestamp_after_kickoff"),
    ):
        try:
            normalize_dataset("games", [{**game(), field: value}], "nfl", "2025", 2)
        except SnapshotError as exc:
            assert code in str(exc) and "game_id=target" in str(exc) and "source=" in str(exc)
        else:
            raise AssertionError(f"normalization accepted {field}={value}")


def test_market_quotes_share_explicit_cutoff_boundary():
    target = {**game(kickoff="2025-09-14T17:00:00Z"),
              "prediction_cutoff": "2025-09-13T17:00:00Z"}
    rows = [{"id": name, "captured_at": stamp} for name, stamp in (
        ("eligible", "2025-09-13T16:00:00Z"),
        ("saturday-late", "2025-09-13T18:00:00Z"),
        ("sunday", "2025-09-14T16:00:00Z"))]
    eligible, diagnostic = filter_market_quotes(target, rows)
    assert [row["id"] for row in eligible] == ["eligible"]
    assert diagnostic["rejected_future"] == 2
    team_result = filter_game_history(target, [team("eligible-history", "2025-09-13T16:00:00Z"),
                                                team("late-history", "2025-09-13T18:00:00Z")], dataset="team")
    assert [row["game_id"] for row in team_result.rows] == ["eligible-history"]


def test_v2_provider_view_preserves_league_wide_features(tmp_path):
    target = game(kickoff="2025-10-01T20:00:00Z")
    target["prediction_cutoff"] = "2025-10-01T19:00:00Z"
    rows = []
    teams = ("BUF", "MIA", "NYJ", "DAL")
    for week in range(1, 5):
        stamp = f"2025-09-{week:02d}T20:00:00Z"
        for index, name in enumerate(teams):
            opponent = teams[(index + 1) % len(teams)]
            rows.append({**team(f"g-{week}-{name}", stamp, week=week, name=name),
                         "opponent": opponent, "home_away": "home" if index % 2 == 0 else "away",
                         "points_for": 20 + index + week, "points_against": 17 + index})
    directory = snapshot_week_dir(tmp_path, "nfl", 2025, 5)
    directory.mkdir(parents=True)
    (directory / "team_stats.json").write_text(__import__("json").dumps(rows))
    provider = HistoricalSnapshotProvider(tmp_path)
    views = provider.get_game_histories("nfl", "2025", 5, target)
    assert {r["team"] for r in views.league_team_history.rows} == set(teams)
    assert {r["team"] for r in views.target_team_history.rows} == {"BUF", "MIA"}
    direct = NFLGameMarketPredictorV2().project(target, rows)
    through_evaluator = NFLGameMarketPredictorV2().project(target, views.league_team_history.rows)
    assert direct is not None and through_evaluator is not None
    assert (direct.home_points, direct.away_points, direct.expected_margin, direct.expected_total) == (
        through_evaluator.home_points, through_evaluator.away_points,
        through_evaluator.expected_margin, through_evaluator.expected_total)
    for feature in ("home_elo", "away_elo", "home_offensive_strength", "away_defensive_strength"):
        assert direct.features[feature] == through_evaluator.features[feature]
