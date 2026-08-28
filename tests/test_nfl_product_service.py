from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone


def _game(status="scheduled"):
    return {
        "game_id": "espn-test", "season": 2026, "week": 1,
        "season_type": "regular",
        "kickoff_time": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "home_team": "BUF", "away_team": "MIA", "home_name": "Buffalo Bills",
        "away_name": "Miami Dolphins", "home_score": 0, "away_score": 0,
        "venue": "Test Stadium", "broadcast": [], "status": status,
    }


def test_weekly_profiles_keep_every_game_and_only_change_recommendation(monkeypatch):
    import backend.app.services.nfl_product_service as service

    monkeypatch.setattr(service, "_schedule", lambda season, week, season_type="regular": [_game()])
    monkeypatch.setattr(service, "_odds_by_game", lambda bucket: {})
    monkeypatch.setattr(service, "_save_prediction", lambda *args: None)

    safe = service.weekly_board(2026, 1, "SAFE", 4)
    aggressive = service.weekly_board(2026, 1, "AGGRESSIVE", 4)

    assert safe["gameCount"] == aggressive["gameCount"] == 1
    assert safe["items"][0]["predictionStatus"] == "available"
    assert safe["items"][0]["winner"] == aggressive["items"][0]["winner"]
    assert safe["items"][0]["winProbability"] == aggressive["items"][0]["winProbability"]
    assert 0 <= safe["items"][0]["homeWinProbability"] <= 1
    assert 0 <= safe["items"][0]["awayWinProbability"] <= 1
    assert safe["items"][0]["homeWinProbability"] + safe["items"][0]["awayWinProbability"] == 1
    assert int(aggressive["items"][0]["recommended"]) >= int(safe["items"][0]["recommended"])
    assert safe["items"][0]["confidenceMetric"] == "evidence_score_not_probability"
    assert safe["items"][0]["evidenceScore"] == safe["items"][0]["confidence"]


def test_schedule_uses_espn_cdn_when_scoreboard_is_blocked(monkeypatch):
    import backend.app.services.nfl_product_service as service

    event = {
        "id": "401-test",
        "date": "2026-09-13T17:00Z",
        "competitions": [{
            "venue": {"fullName": "Test Stadium"},
            "broadcasts": [{"names": ["CBS"]}],
            "competitors": [
                {"homeAway": "home", "score": "0", "team": {"abbreviation": "BUF", "displayName": "Buffalo Bills"}},
                {"homeAway": "away", "score": "0", "team": {"abbreviation": "MIA", "displayName": "Miami Dolphins"}},
            ],
        }],
        "status": {"type": {"state": "pre", "completed": False}},
    }
    payload = {"content": {"schedule": {"20260913": {"games": [event]}}}}
    requested = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        requested.append(request.full_url)
        if request.full_url.startswith(service.ESPN_SCOREBOARD):
            raise OSError("scoreboard blocked")
        return Response()

    service._provider_schedule.cache_clear()
    monkeypatch.setattr(service, "urlopen", fake_urlopen)
    games = service._provider_schedule(2026, 1, "preseason")

    assert len(requested) == 2
    assert requested[1].startswith(service.ESPN_SCHEDULE_CDN)
    assert "seasontype=1" in requested[1]
    assert games[0]["home_team"] == "BUF"
    assert games[0]["away_team"] == "MIA"
    assert games[0]["status"] == "scheduled"


def test_preseason_provider_weeks_are_normalized_to_hof_and_display_weeks(monkeypatch):
    import backend.app.services.nfl_product_service as service

    def provider_schedule(season, provider_week, season_type="regular"):
        assert season == 2026
        if season_type != "preseason" or provider_week > 4:
            return []
        count = 1 if provider_week == 1 else 16
        starts = {1: "2026-08-07T00:00Z", 2: "2026-08-13T23:00Z",
                  3: "2026-08-21T00:00Z", 4: "2026-08-27T23:00Z"}
        return [{
            **_game(), "game_id": f"p{provider_week}-{index}",
            "provider_week": provider_week, "season_type": "preseason",
            "kickoff_time": starts[provider_week],
        } for index in range(count)]

    monkeypatch.setattr(service, "_provider_schedule", provider_schedule)
    service._preseason_week_mapping.cache_clear()

    hof = service._schedule(2026, 0, "preseason")
    week_one = service._schedule(2026, 1, "preseason")
    week_three = service._schedule(2026, 3, "preseason")

    assert len(hof) == 1
    assert (hof[0]["week_key"], hof[0]["display_week"], hof[0]["provider_week"]) == ("HOF", 0, 1)
    assert len(week_one) == len(week_three) == 16
    assert (week_one[0]["week_key"], week_one[0]["display_week"], week_one[0]["provider_week"]) == ("PRE1", 1, 2)
    assert (week_three[0]["week_key"], week_three[0]["display_week"], week_three[0]["provider_week"]) == ("PRE3", 3, 4)
    assert hof[0]["kickoff_time"].startswith("2026-08-07")
    assert week_one[0]["kickoff_time"].startswith("2026-08-13")
    assert week_three[0]["kickoff_time"].startswith("2026-08-27")


def test_preseason_without_standalone_opener_does_not_apply_an_offset(monkeypatch):
    import backend.app.services.nfl_product_service as service

    def provider_schedule(season, provider_week, season_type="regular"):
        return [{**_game(), "provider_week": provider_week} for _ in range(16)] if provider_week in {1, 2, 3} else []

    monkeypatch.setattr(service, "_provider_schedule", provider_schedule)
    service._preseason_week_mapping.cache_clear()

    week_one = service._schedule(2027, 1, "preseason")
    assert len(week_one) == 16
    assert week_one[0]["provider_week"] == week_one[0]["display_week"] == 1
    assert week_one[0]["week_key"] == "PRE1"


def test_regular_week_one_keeps_provider_identity_and_september_date(monkeypatch):
    import backend.app.services.nfl_product_service as service

    monkeypatch.setattr(service, "_provider_schedule", lambda season, provider_week, season_type="regular": [{
        **_game(), "provider_week": provider_week, "season_type": season_type,
        "kickoff_time": "2026-09-10T00:20Z",
    }])
    game = service._schedule(2026, 1, "regular")[0]
    assert (game["week_key"], game["display_week"], game["provider_week"]) == ("REG1", 1, 1)
    assert game["kickoff_time"].startswith("2026-09-10")


def test_schedule_refresh_window_advances_without_restart(monkeypatch):
    import backend.app.services.nfl_product_service as service

    windows = []
    monkeypatch.setattr(
        service, "_provider_schedule_cached",
        lambda season, week, season_type, refresh_window: windows.append(refresh_window) or [],
    )
    monkeypatch.setattr(service, "time", lambda: 1000)
    service._provider_schedule(2027, 1, "regular")
    monkeypatch.setattr(service, "time", lambda: 1000 + service.SCHEDULE_REFRESH_SECONDS)
    service._provider_schedule(2027, 1, "regular")
    assert windows == [3, 4]


def test_current_context_automatically_advances_to_next_provider_slate(monkeypatch):
    import backend.app.services.nfl_product_service as service

    now = datetime.now(timezone.utc)
    def game(week, status, offset):
        return {
            **_game(status), "week": week, "display_week": week,
            "provider_week": week + 1, "week_key": f"PRE{week}",
            "week_label": f"Preseason Week {week}",
            "kickoff_time": (now + timedelta(days=offset)).isoformat(),
        }
    slates = {
        1: [game(1, "final", -7)],
        2: [game(2, "scheduled", 1)],
        3: [game(3, "scheduled", 8)],
    }
    monkeypatch.setattr(
        service, "_schedule",
        lambda season, week, season_type="regular": slates.get(week, [])
        if season_type == "preseason" else [],
    )
    assert service.current_week_context(2027)["weekKey"] == "PRE2"
    slates[2] = [game(2, "final", -1)]
    assert service.current_week_context(2027)["weekKey"] == "PRE3"


def test_historical_games_never_reconstruct_missing_pregame_prediction(monkeypatch, tmp_path):
    import backend.app.services.nfl_product_service as service

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'history.db').as_posix()}")
    final = _game("final")
    final.update(home_score=27, away_score=20)
    monkeypatch.setattr(service, "_schedule", lambda season, week, season_type="regular": [final] if week == 1 else [])

    rows = service.historical_games(2026, 8)

    assert len(rows) == 1
    assert rows[0]["actualWinner"] == "BUF"
    assert rows[0]["prediction"] is None
    assert rows[0]["predictionResult"] is None


def test_final_games_are_not_retroactively_predicted(monkeypatch):
    import backend.app.services.nfl_product_service as service

    final = _game("final")
    final.update(home_score=27, away_score=20)
    monkeypatch.setattr(service, "_schedule", lambda season, week, season_type="regular": [final])
    monkeypatch.setattr(service, "_odds_by_game", lambda bucket: {})
    monkeypatch.setattr(service, "_prediction_snapshots", lambda *args: {})

    board = service.weekly_board(2026, 1, "BALANCED", 7)

    assert board["items"][0]["predictionStatus"] == "unavailable"
    assert "never re-predicted" in board["items"][0]["unavailableReason"]
    assert "winner" not in board["items"][0]


def test_scheduled_game_displays_first_global_snapshot_without_recalculation(monkeypatch):
    import backend.app.services.nfl_product_service as service

    snapshot = {
        "winner": "MIA", "winProbability": .58, "homeWinProbability": .42,
        "awayWinProbability": .58, "seasonType": "regular",
        "profileEligibility": {"SAFE": False, "BALANCED": True, "AGGRESSIVE": True},
        "market": {"homeOdds": 110, "awayOdds": -120},
    }
    monkeypatch.setattr(service, "_schedule", lambda *args: [_game()])
    monkeypatch.setattr(service, "_odds_by_game", lambda bucket: {})
    monkeypatch.setattr(
        service, "_prediction_snapshots",
        lambda user_id, *args, **kwargs: {"espn-test": snapshot} if user_id == 0 else {},
    )
    monkeypatch.setattr(service, "_save_prediction", lambda *args: (_ for _ in ()).throw(AssertionError("must not rewrite")))

    board = service.weekly_board(2026, 1, "BALANCED", 99)
    item = board["items"][0]
    assert item["predictionStatus"] == "pregame_snapshot"
    assert item["winner"] == "MIA"
    assert item["recommendedBet"] is True


def test_live_market_overlay_changes_edge_not_frozen_model_probability(monkeypatch):
    import backend.app.services.nfl_product_service as service

    snapshot = {
        "winner": "NE", "winProbability": .59, "homeWinProbability": .41,
        "awayWinProbability": .59, "seasonType": "preseason", "rating": 6.8,
        "profileEligibility": {"SAFE": False, "BALANCED": False, "AGGRESSIVE": False},
        "market": {"awayOdds": None, "homeOdds": None, "coverage": {"moneyline": False}},
    }
    target = _game()
    target.update(home_team="CLE", away_team="NE", season_type="preseason", week=3)
    current_market = {
        "awayOdds": 140, "homeOdds": -160, "awayImpliedProbability": .40,
        "homeImpliedProbability": .60, "coverage": {"moneyline": True},
    }
    monkeypatch.setattr(service, "_schedule", lambda *args: [target])
    monkeypatch.setattr(service, "_odds_by_game", lambda bucket: {})
    monkeypatch.setattr(
        service, "_market_context",
        lambda rows, home, away: current_market,
    )
    monkeypatch.setattr(service, "_save_market_observation", lambda *args: None)
    monkeypatch.setattr(
        service, "_market_history",
        lambda game_id: {"count": 1, "first": {"market": current_market},
                         "latest": {"market": current_market}, "closing": None,
                         "closingStatus": "pending"},
    )
    monkeypatch.setattr(
        service, "_prediction_snapshots",
        lambda user_id, *args, **kwargs: {"espn-test": snapshot} if user_id == 0 else {},
    )

    item = service.weekly_board(2026, 3, "BALANCED", 99, season_type="preseason")["items"][0]
    assert item["winProbability"] == .59
    assert item["edge"] == .19
    assert item["recommendedBet"] is True
    assert item["modelRating"] == 6.8
    assert item["frozenMarketAtModelGeneration"]["awayOdds"] is None


def test_preseason_board_uses_only_prior_preseason_results_and_caps_uncertainty(monkeypatch):
    import backend.app.services.nfl_product_service as service

    target = _game()
    target.update(week=3, season_type="preseason", home_team="BUF", away_team="MIA")
    prior = [
        {**_game("final"), "game_id": "w1", "week": 1, "season_type": "preseason", "home_team": "BUF", "away_team": "NYJ", "home_score": 24, "away_score": 10},
        {**_game("final"), "game_id": "w2", "week": 2, "season_type": "preseason", "home_team": "MIA", "away_team": "TB", "home_score": 17, "away_score": 14},
    ]

    def schedule(season, week, season_type="regular"):
        assert season_type == "preseason"
        return [target] if week == 3 else [game for game in prior if game["week"] == week]

    monkeypatch.setattr(service, "_schedule", schedule)
    monkeypatch.setattr(service, "_odds_by_game", lambda bucket: {})
    monkeypatch.setattr(service, "_prediction_snapshots", lambda *args: {})
    monkeypatch.setattr(service, "_save_prediction", lambda *args: None)

    board = service.weekly_board(2026, 3, "BALANCED", 7, season_type="preseason")
    pick = board["items"][0]

    assert board["seasonType"] == "preseason"
    assert board["modelVersion"] == "nfl_preseason_score_v1"
    assert board["startDate"] == board["endDate"] == target["kickoff_time"]
    assert .5 <= pick["winProbability"] <= .64
    assert pick["evidenceScore"] <= 55
    assert "regular-season power ratings" in " ".join(pick["reasons"]).lower()
    assert "QB rotation" in pick["missingData"]
    assert pick["recommendedBet"] is False  # a winner lean is not value without a price


def test_tied_final_is_graded_as_push(monkeypatch, tmp_path):
    import backend.app.services.nfl_product_service as service

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'tie.db').as_posix()}")
    final = _game("final")
    final.update(home_score=20, away_score=20)
    monkeypatch.setattr(service, "_schedule", lambda season, week, season_type="regular": [final] if week == 1 else [])
    monkeypatch.setattr(service, "_prediction_snapshots", lambda *args, **kwargs: {"espn-test": {"winner": "BUF", "seasonType": "regular"}})

    row = service.historical_games(2026, 8)[0]

    assert row["actualWinner"] is None
    assert row["predictionResult"] == "push"


def test_final_lean_is_no_bet_when_profile_was_ineligible(monkeypatch):
    import backend.app.services.nfl_product_service as service

    final = _game("final")
    final.update(home_score=27, away_score=20)
    snapshot = {"winner": "BUF", "seasonType": "regular",
                "profileEligibility": {"SAFE": False, "BALANCED": True, "AGGRESSIVE": True}}
    monkeypatch.setattr(service, "_schedule", lambda season, week, season_type="regular": [final] if week == 1 else [])
    monkeypatch.setattr(service, "_prediction_snapshots", lambda *args, **kwargs: {"espn-test": snapshot})

    row = service.historical_games(2026, 8)[0]
    assert row["predictionResult"] == "hit"
    assert row["betGrades"] == {"SAFE": "NO_BET", "BALANCED": "WIN", "AGGRESSIVE": "WIN"}


def test_week_performance_reports_missing_snapshot_instead_of_inventing_metrics(monkeypatch):
    import backend.app.services.nfl_product_service as service

    monkeypatch.setattr(service, "historical_games", lambda *args: [])
    report = service.prediction_performance(2026, 2, 8, "preseason")

    assert report["predictions"] == report["wins"] == report["losses"] == report["pushes"] == 0
    assert report["accuracy"] is None
    assert report["roi"] is None
    assert report["calibrationStatus"] == "INSUFFICIENT_DATA"


def test_week_performance_uses_only_stored_pregame_price_for_roi(monkeypatch):
    import backend.app.services.nfl_product_service as service

    rows = [
        {"week": 2, "home_team": "BUF", "away_team": "MIA", "predictionResult": "hit",
         "prediction": {"winner": "BUF", "winProbability": .62, "riskLevel": "BALANCED",
                        "market": {"homeOdds": -125, "awayOdds": 110}}},
        {"week": 2, "home_team": "KC", "away_team": "LV", "predictionResult": "miss",
         "prediction": {"winner": "LV", "winProbability": .55, "riskLevel": "AGGRESSIVE",
                        "market": {"homeOdds": -120, "awayOdds": 105}}},
    ]
    monkeypatch.setattr(service, "historical_games", lambda *args: rows)

    report = service.prediction_performance(2026, 2, 8, "preseason")

    assert report["units"] == -.2
    assert report["roi"] == -10.0
    assert report["roiCoverage"] == {"pricedPredictions": 2, "totalPredictions": 2}
    assert report["calibrationBuckets"]


def test_multi_game_parlay_uses_distinct_verified_model_winners(monkeypatch):
    import backend.app.services.nfl_product_service as service

    market_timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    items = []
    for index, (winner, probability, odds, implied) in enumerate([
        ("BUF", .64, -125, .55), ("KC", .61, 105, .49), ("PHI", .59, 110, .48), ("DAL", .56, 115, .47),
    ], 1):
        items.append({
            "game_id": f"g{index}", "status": "scheduled", "predictionStatus": "available",
            "winner": winner, "winProbability": probability, "home_team": winner,
            "away_team": f"X{index}", "recommendedBet": True,
            "market": {"homeOdds": odds, "awayOdds": -110,
            "homeImpliedProbability": implied, "awayImpliedProbability": 1-implied,
            "sportsbook": "Book", "provider": "the-odds-api",
            "marketTimestamp": market_timestamp},
        })
    monkeypatch.setattr(service, "weekly_board", lambda *args, **kwargs: {"items": items})
    result, rejected = service.build_multi_game_parlay(
        season=2026, week=3, season_type="preseason", profile="BALANCED",
        selections=[{"gameId": row["game_id"], "team": row["winner"]} for row in items], user_id=7,
    )

    assert len(result.parlay.legs) == 4
    assert len({leg.team for leg in result.parlay.legs}) == 4
    assert result.estimated_odds is not None
    assert 0 < result.combined_probability < 1
    assert rejected == []


def test_multi_game_parlay_rejects_missing_prices_and_opposing_picks(monkeypatch):
    import backend.app.services.nfl_product_service as service

    items = [{"game_id": "g1", "status": "scheduled", "predictionStatus": "available",
              "winner": "BUF", "winProbability": .65, "home_team": "BUF", "away_team": "MIA",
              "market": {"homeOdds": None, "awayOdds": 120, "homeImpliedProbability": None,
                         "awayImpliedProbability": .45, "sportsbook": None,
                         "provider": "the-odds-api", "marketTimestamp": datetime.now(timezone.utc).isoformat()}}]
    monkeypatch.setattr(service, "weekly_board", lambda *args, **kwargs: {"items": items})

    result, rejected = service.build_multi_game_parlay(
        season=2026, week=3, season_type="preseason", profile="SAFE",
        selections=[{"gameId": "g1", "team": "MIA"}, {"gameId": "g1", "team": "BUF"}], user_id=7,
    )

    assert result.parlay.legs == []
    assert {row["reason"] for row in rejected} == {"selection_conflicts_with_model_winner", "verified_price_unavailable"}


def test_multi_game_parlay_rejects_mock_stale_and_pass_legs(monkeypatch):
    import backend.app.services.nfl_product_service as service

    fresh = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0).isoformat()
    base = {"status": "scheduled", "predictionStatus": "pregame_snapshot",
            "winner": "BUF", "winProbability": .65, "home_team": "BUF", "away_team": "MIA"}
    items = [
        {**base, "game_id": "mock", "recommendedBet": True,
         "market": {"homeOdds": -110, "awayOdds": 100, "homeImpliedProbability": .52,
                    "awayImpliedProbability": .48, "sportsbook": "sample", "provider": "sample",
                    "marketTimestamp": fresh}},
        {**base, "game_id": "stale", "recommendedBet": True,
         "market": {"homeOdds": -110, "awayOdds": 100, "homeImpliedProbability": .52,
                    "awayImpliedProbability": .48, "sportsbook": "Book", "provider": "the-odds-api",
                    "marketTimestamp": stale}},
        {**base, "game_id": "pass", "recommendedBet": False,
         "market": {"homeOdds": -110, "awayOdds": 100, "homeImpliedProbability": .52,
                    "awayImpliedProbability": .48, "sportsbook": "Book", "provider": "the-odds-api",
                    "marketTimestamp": fresh}},
    ]
    monkeypatch.setattr(service, "weekly_board", lambda *args, **kwargs: {"items": items})
    result, rejected = service.build_multi_game_parlay(
        season=2026, week=3, season_type="preseason", profile="BALANCED",
        selections=[{"gameId": row["game_id"], "team": "BUF"} for row in items], user_id=7,
    )
    assert result.parlay.legs == []
    assert {row["reason"] for row in rejected} == {
        "verified_price_unavailable", "stale_price", "model_decision_is_pass",
    }


def test_prediction_snapshot_insert_is_atomic_and_preserves_preseason_model(monkeypatch, tmp_path):
    import backend.app.services.nfl_product_service as service
    from backend.app.database import get_db_connection

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'snapshots.db').as_posix()}")
    game = _game()
    game.update(week=4, season_type="preseason")
    prediction = {"winner": "BUF", "modelVersion": "nfl_preseason_score_v1", "seasonType": "preseason"}
    service._initialize_predictions()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: service._save_prediction(7, game, prediction), range(8)))

    with get_db_connection() as connection:
        rows = connection.execute(
            "SELECT model_version,prediction_json,season_type,display_week,provider_week,provider,week_key "
            "FROM nfl_game_predictions"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["model_version"] == "nfl_preseason_score_v1"
    assert json.loads(rows[0]["prediction_json"])["seasonType"] == "preseason"
    assert (rows[0]["season_type"], rows[0]["display_week"], rows[0]["provider_week"],
            rows[0]["provider"], rows[0]["week_key"]) == ("preseason", 4, 5, "espn", "PRE4")


def test_market_observations_are_append_only_and_do_not_rewrite_model(monkeypatch, tmp_path):
    import backend.app.services.nfl_product_service as service
    from backend.app.database import get_db_connection

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'markets.db').as_posix()}")
    game = {
        **_game(), "display_week": 3, "provider_week": 4, "week": 3,
        "week_key": "PRE3", "season_type": "preseason", "provider": "espn",
    }
    prediction = {
        "winner": "BUF", "winProbability": .5659,
        "modelVersion": "nfl_preseason_score_v1", "seasonType": "preseason",
        "market": {"coverage": {"moneyline": False, "spread": False, "total": False}},
    }
    first = {
        "homeOdds": -120, "awayOdds": 105, "marketTimestamp": "2026-08-25T01:00:00Z",
        "coverage": {"moneyline": True, "spread": False, "total": False},
    }
    second = {
        "homeOdds": -130, "awayOdds": 110, "marketTimestamp": "2026-08-26T01:00:00Z",
        "coverage": {"moneyline": True, "spread": True, "total": True},
    }
    service._save_prediction(0, game, prediction)
    service._save_market_observation(game, first)
    service._save_market_observation(game, second)
    service._save_market_observation(game, second)
    service._save_market_observation(game, {**second, "marketTimestamp": "2026-08-26T01:05:00Z"})

    history = service._market_history(game["game_id"])
    assert history["count"] == 2
    assert history["first"]["market"]["homeOdds"] == -120
    assert history["latest"]["market"]["homeOdds"] == -130
    assert history["closing"] is None
    with get_db_connection() as connection:
        stored = connection.execute(
            "SELECT prediction_json FROM nfl_game_predictions WHERE game_id=?", (game["game_id"],)
        ).fetchone()
    assert json.loads(stored["prediction_json"])["winProbability"] == .5659


def test_depth_charts_are_user_scoped_and_support_crud(monkeypatch, tmp_path):
    import backend.app.services.nfl_product_service as service

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'depth.db').as_posix()}")
    created = service.save_depth_chart(11, "Sunday starters", {"QB": "player-1", "NOT_A_SLOT": "bad"})

    assert created["formation"] == {"QB": "player-1"}
    assert service.list_depth_charts(12) == []

    updated = service.save_depth_chart(11, "Updated", {"RB": "player-2"}, created["id"])
    assert updated["name"] == "Updated"
    assert updated["formation"] == {"RB": "player-2"}

    service.delete_depth_chart(11, created["id"])
    assert service.list_depth_charts(11) == []
