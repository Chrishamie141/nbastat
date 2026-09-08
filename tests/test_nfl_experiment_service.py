from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO
from urllib.error import HTTPError

import pytest


def _snapshot(winner="BUF", probability=.62, rating=7.1):
    return {
        "winner": winner, "winProbability": probability, "rating": rating,
        "homeWinProbability": probability if winner == "BUF" else 1 - probability,
        "awayWinProbability": 1 - probability if winner == "BUF" else probability,
        "reasons": ["Frozen pregame reason"], "evidenceScore": 51,
        "mainRisk": "Frozen pregame risk", "missingData": ["QB rotation"],
        "dataAsOf": "2029-12-31T00:00:00Z", "seasonType": "regular",
        "modelVersion": "synthetic_model_v1", "profileEligibility": {
            "SAFE": False, "BALANCED": False, "AGGRESSIVE": False,
        }, "market": {"coverage": {"moneyline": False, "spread": False, "total": False}},
    }


def _game(game_id, *, status="scheduled", home_score=None, away_score=None,
          kickoff="2030-09-01T23:00:00Z"):
    return {
        "game_id": game_id, "season": 2030, "season_type": "regular", "week": 1,
        "display_week": 1, "provider_week": 1, "week_key": "REG1", "week_label": "Week 1",
        "kickoff_time": kickoff, "home_team": "BUF", "away_team": "MIA",
        "home_name": "Buffalo Bills", "away_name": "Miami Dolphins",
        "home_score": home_score, "away_score": away_score, "status": status,
        "provider": "espn", "venue": "Test Stadium", "broadcast": [],
    }


def _market(home_odds=-200, away_odds=170, at="2030-09-01T22:00:00Z"):
    return {
        "homeOdds": home_odds, "awayOdds": away_odds, "sportsbook": "Verified Book",
        "provider": "the-odds-api", "marketTimestamp": at,
        "coverage": {"moneyline": True, "spread": False, "total": False},
    }


def _insert_snapshot(service, game, prediction):
    service._save_prediction(0, game, prediction)


def _insert_market(product, game, market, retrieved="2030-09-01T22:00:01Z"):
    from backend.app.database import get_db_connection

    product._initialize_predictions()
    with get_db_connection() as connection:
        return product._insert_market_observation(
            connection, game_id=game["game_id"], season=game["season"],
            season_type=game["season_type"], display_week=game["display_week"],
            provider_week=game["provider_week"], kickoff_time=game["kickoff_time"],
            retrieved_at=retrieved, market=market,
        )


def test_closing_boundary_is_strict_and_timezone_aware():
    from backend.app.services.nfl_experiment_service import is_strictly_pregame

    kickoff = "2030-09-01T19:00:00-04:00"
    assert is_strictly_pregame(market_timestamp="2030-09-01T22:59:59Z",
                               retrieved_at="2030-09-01T22:59:59Z", kickoff_timestamp=kickoff)
    assert not is_strictly_pregame(market_timestamp="2030-09-01T23:00:00Z",
                                   retrieved_at="2030-09-01T22:59:59Z", kickoff_timestamp=kickoff)
    assert not is_strictly_pregame(market_timestamp="2030-09-01T23:00:01Z",
                                   retrieved_at="2030-09-01T23:00:01Z", kickoff_timestamp=kickoff)


def test_updated_and_postponed_kickoff_control_the_closing_boundary(monkeypatch, tmp_path):
    import backend.app.services.nfl_product_service as product
    from backend.app.services import nfl_experiment_service as experiment

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'kickoff.db').as_posix()}")
    moved_earlier = _game("moved-earlier", kickoff="2030-09-01T23:00:00Z")
    experiment.record_schedule_game(moved_earlier, "2030-08-01T00:00:00Z")
    moved_earlier["kickoff_time"] = "2030-09-01T22:00:00Z"
    experiment.record_schedule_game(moved_earlier, "2030-08-02T00:00:00Z")
    assert not _insert_market(product, moved_earlier, _market(at="2030-09-01T22:30:00Z"),
                              "2030-09-01T22:30:01Z")

    postponed = _game("postponed", kickoff="2030-09-01T23:00:00Z")
    experiment.record_schedule_game(postponed, "2030-08-01T00:00:00Z")
    postponed["kickoff_time"] = "2030-09-02T23:00:00Z"
    experiment.record_schedule_game(postponed, "2030-08-02T00:00:00Z")
    assert _insert_market(product, postponed, _market(at="2030-09-02T20:00:00Z"),
                          "2030-09-02T20:00:01Z")
    history = experiment.market_history_for_game(postponed["game_id"])
    assert history["count"] == 1
    assert history["operationalKickoff"] == "2030-09-02T23:00:00Z"


def test_weekly_board_context_uses_one_connection_for_the_full_slate(monkeypatch, tmp_path):
    import backend.app.services.nfl_product_service as product
    from backend.app.services import nfl_experiment_service as experiment

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'batch.db').as_posix()}")
    product._initialize_predictions()
    experiment.initialize_experiment_database()
    original_connection = experiment.get_db_connection
    connection_count = 0

    def counted_connection():
        nonlocal connection_count
        connection_count += 1
        return original_connection()

    monkeypatch.setattr(experiment, "initialize_experiment_database", lambda: None)
    monkeypatch.setattr(experiment, "get_db_connection", counted_connection)

    contexts = experiment.weekly_board_context([_game("batch-1"), _game("batch-2")])

    assert connection_count == 1
    assert set(contexts) == {"batch-1", "batch-2"}
    assert all(value["count"] == 0 for value in contexts.values())


def test_hash_mismatch_fails_closed_before_official_grading(monkeypatch, tmp_path):
    import backend.app.services.nfl_product_service as product
    from backend.app.database import get_db_connection
    from backend.app.services import nfl_experiment_service as experiment

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'integrity.db').as_posix()}")
    game = _game("integrity")
    _insert_snapshot(product, game, _snapshot())
    digest, _ = experiment.compute_baseline_hash(2030, "regular", 1)
    experiment.register_experiment(season=2030, season_type="regular", display_week=1,
                                   expected_hash=digest, model_version="synthetic_model_v1")
    final = {**game, "status": "final", "home_score": 24, "away_score": 17}
    experiment.record_schedule_game(final)
    with get_db_connection() as connection:
        connection.execute(
            "UPDATE nfl_game_predictions SET prediction_json=? WHERE game_id=?",
            (json.dumps({**_snapshot(), "winProbability": .99}), game["game_id"]),
        )
    with pytest.raises(experiment.ExperimentIntegrityError):
        experiment.grade_experiment(2030, "regular", 1)
    with get_db_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM nfl_experiment_grades").fetchone()[0] == 0


def test_complete_synthetic_grading_dry_run_is_flat_unit_and_idempotent(monkeypatch, tmp_path):
    import backend.app.services.nfl_product_service as product
    from backend.app.database import get_db_connection
    from backend.app.services import nfl_experiment_service as experiment

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'dry-run.db').as_posix()}")
    cases = [
        # id, probability, odds, final home/away, expected wager result
        ("correct-favorite", .65, (-200, 150), (24, 17), "WIN"),
        ("incorrect-favorite", .65, (-200, 150), (17, 24), "LOSS"),
        ("underdog-win", .58, (160, -180), (24, 17), "WIN"),
        ("tie", .65, (-200, 150), (20, 20), "PUSH"),
        ("missing-final", .65, (-200, 150), None, "PENDING"),
        ("missing-market", .65, None, (24, 17), "NO_BET"),
        ("positive-winner", .58, (140, -160), (24, 17), "WIN"),
        ("negative-winner", .65, (-200, 150), (24, 17), "WIN"),
        ("qualified-loss", .65, (-200, 150), (14, 21), "LOSS"),
        ("pass-correct", .55, (-110, -110), (21, 17), "NO_BET"),
    ]
    frozen_json = {}
    for game_id, probability, odds, score, _ in cases:
        game = _game(game_id)
        prediction = _snapshot(probability=probability)
        _insert_snapshot(product, game, prediction)
        frozen_json[game_id] = json.dumps(prediction, sort_keys=True)
        if odds:
            _insert_market(product, game, _market(*odds))
        if score:
            experiment.record_schedule_game({**game, "status": "final",
                                             "home_score": score[0], "away_score": score[1]})
        else:
            experiment.record_schedule_game(game)
    digest, count = experiment.compute_baseline_hash(2030, "regular", 1)
    assert count == 10
    experiment.register_experiment(season=2030, season_type="regular", display_week=1,
                                   expected_hash=digest, model_version="synthetic_model_v1")

    first = experiment.grade_experiment(2030, "regular", 1)
    second = experiment.grade_experiment(2030, "regular", 1)
    assert first["inserted"] == 9
    assert second == {**second, "inserted": 0, "duplicatesPrevented": 9}
    with get_db_connection() as connection:
        grades = {row["game_id"]: row for row in connection.execute(
            "SELECT * FROM nfl_experiment_grades"
        ).fetchall()}
        stored = {row["game_id"]: row["prediction_json"] for row in connection.execute(
            "SELECT game_id,prediction_json FROM nfl_game_predictions"
        ).fetchall()}
    assert grades["correct-favorite"]["net_units"] == .5
    assert grades["positive-winner"]["net_units"] == 1.4
    assert grades["qualified-loss"]["net_units"] == -1
    assert grades["tie"]["wager_result"] == "PUSH"
    assert grades["missing-market"]["prediction_result"] == "WIN"
    assert grades["missing-market"]["wager_result"] == "NO_BET"
    assert grades["pass-correct"]["prediction_result"] == "WIN"
    assert grades["pass-correct"]["wager_result"] == "NO_BET"
    assert "missing-final" not in grades
    assert stored == frozen_json


def test_american_odds_flat_unit_examples():
    from backend.app.services.nfl_experiment_service import american_profit

    assert american_profit(140) == 1.4
    assert american_profit(-200) == .5


def test_probability_buckets_are_fixed_and_flag_small_samples():
    from backend.app.services.nfl_experiment_service import _calibration

    rows = [{"probability": value, "predictionResult": result} for value, result in (
        (.50, "WIN"), (.5499, "LOSS"), (.55, "PUSH"), (.60, "WIN"), (.65, "LOSS"),
    )]
    buckets = _calibration(rows)
    assert [row["range"] for row in buckets] == [
        "50.00-54.99%", "55.00-59.99%", "60.00-64.99%", "65.00%+",
    ]
    assert all(row["sampleStatus"] == "INSUFFICIENT_SAMPLE" for row in buckets)


def test_odds_provider_quota_and_malformed_response_are_distinct(monkeypatch):
    import nfl_data_service as data

    monkeypatch.setattr(data, "_odds_key", lambda: "configured-secret")
    body = BytesIO(json.dumps({"message": "Usage quota reached", "error_code": "OUT_OF_USAGE_CREDITS"}).encode())
    monkeypatch.setattr(data, "_fetch_json", lambda url: (_ for _ in ()).throw(
        HTTPError(url, 401, "Unauthorized", None, body)
    ))
    rows, health = data.fetch_nfl_team_lines_live()
    assert rows == []
    assert health["state"] == "QUOTA_EXHAUSTED"
    assert health["safe_error_code"] == "OUT_OF_USAGE_CREDITS"

    monkeypatch.setattr(data, "_fetch_json", lambda url: {"unexpected": "shape"})
    _, malformed = data.fetch_nfl_team_lines_live()
    assert malformed["state"] == "MALFORMED_RESPONSE"


@pytest.mark.parametrize(("status", "body", "expected"), [
    (401, {"error_code": "INVALID_KEY"}, "AUTH_ERROR"),
    (429, {"error_code": "RATE_LIMIT"}, "RATE_LIMITED"),
    (503, {"error_code": "TEMPORARY"}, "UPSTREAM_ERROR"),
])
def test_odds_provider_http_states_are_classified(monkeypatch, status, body, expected):
    import nfl_data_service as data

    monkeypatch.setattr(data, "_odds_key", lambda: "configured-secret")
    stream = BytesIO(json.dumps(body).encode())
    monkeypatch.setattr(data, "_fetch_json", lambda url: (_ for _ in ()).throw(
        HTTPError(url, status, "provider failure", None, stream)
    ))
    _, health = data.fetch_nfl_team_lines_live()
    assert health["state"] == expected


def test_odds_provider_timeout_empty_and_stale_states(monkeypatch):
    import nfl_data_service as data

    monkeypatch.setattr(data, "_odds_key", lambda: "configured-secret")
    monkeypatch.setattr(data, "_fetch_json", lambda url: (_ for _ in ()).throw(TimeoutError()))
    assert data.fetch_nfl_team_lines_live()[1]["state"] == "TIMEOUT"
    monkeypatch.setattr(data, "_fetch_json", lambda url: [])
    assert data.fetch_nfl_team_lines_live()[1]["state"] == "NO_MARKET_AVAILABLE"
    monkeypatch.setattr(data, "_fetch_json", lambda url: [{
        "id": "g", "home_team": "Buffalo Bills", "away_team": "Miami Dolphins",
        "bookmakers": [{"title": "Book", "markets": [{"key": "h2h",
            "last_update": "2020-01-01T00:00:00Z", "outcomes": [
                {"name": "Buffalo Bills", "price": -110},
                {"name": "Miami Dolphins", "price": 100},
            ]}]}],
    }])
    assert data.fetch_nfl_team_lines_live()[1]["state"] == "STALE_DATA"


def test_schedule_live_final_graded_lifecycle_preserves_snapshot(monkeypatch, tmp_path):
    import backend.app.services.nfl_product_service as product
    from backend.app.database import get_db_connection
    from backend.app.services import nfl_experiment_service as experiment

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'lifecycle.db').as_posix()}")
    scheduled = _game("lifecycle")
    prediction = _snapshot(probability=.65)
    _insert_snapshot(product, scheduled, prediction)
    _insert_market(product, scheduled, _market(-200, 150))
    digest, _ = experiment.compute_baseline_hash(2030, "regular", 1)
    experiment.register_experiment(season=2030, season_type="regular", display_week=1,
                                   expected_hash=digest, model_version="synthetic_model_v1")
    experiment.record_schedule_game(scheduled, "2030-08-01T00:00:00Z")
    experiment.record_schedule_game({**scheduled, "status": "live", "home_score": 7, "away_score": 3},
                                    "2030-09-01T23:30:00Z")
    experiment.record_schedule_game({**scheduled, "status": "final", "home_score": 24, "away_score": 17},
                                    "2030-09-02T02:30:00Z")
    assert not _insert_market(product, scheduled, _market(at="2030-09-01T23:00:00Z"),
                              "2030-09-01T23:00:00Z")
    first = experiment.grade_experiment(2030, "regular", 1)
    second = experiment.grade_experiment(2030, "regular", 1)
    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert second["duplicatesPrevented"] == 1
    with get_db_connection() as connection:
        states = [row["current_value"] for row in connection.execute(
            "SELECT current_value FROM nfl_schedule_changes WHERE game_id=? AND field_name='status' ORDER BY id",
            (scheduled["game_id"],),
        ).fetchall()]
        result = connection.execute("SELECT * FROM nfl_game_results WHERE game_id=?",
                                    (scheduled["game_id"],)).fetchone()
        frozen = connection.execute("SELECT prediction_json FROM nfl_game_predictions WHERE game_id=?",
                                    (scheduled["game_id"],)).fetchone()
    assert states == ["live", "final"]
    assert (result["home_score"], result["away_score"]) == (24, 17)
    assert frozen["prediction_json"] == json.dumps(prediction, sort_keys=True)


def test_prediction_cannot_be_created_after_kickoff(monkeypatch, tmp_path):
    import backend.app.services.nfl_product_service as product
    from backend.app.database import get_db_connection

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'post-kickoff.db').as_posix()}")
    final = _game("already-started", status="final", kickoff="2020-01-01T00:00:00Z")
    product._save_prediction(0, final, _snapshot())
    product._initialize_predictions()
    with get_db_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM nfl_game_predictions").fetchone()[0] == 0


def test_internal_dashboard_access_requires_durable_database_flag(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from backend.app.database import get_db_connection, initialize_auth_database
    from backend.app.services.auth_service import create_token
    from backend.app.services.entitlement_service import require_internal_access

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'internal.db').as_posix()}")
    monkeypatch.setenv("AUTH_SECRET", "internal-test-secret")
    initialize_auth_database()
    with get_db_connection() as connection:
        connection.execute(
            """INSERT INTO users(id,name,email,password_hash,created_at,updated_at,is_active,is_internal)
            VALUES (1,'Operator','operator@example.com','x','now','now',1,0)"""
        )

    class Request:
        cookies = {"sbs_session": create_token(1)}

    with pytest.raises(HTTPException) as denied:
        require_internal_access(Request())
    assert denied.value.status_code == 403
    monkeypatch.setenv("INTERNAL_ADMIN_EMAILS", "operator@example.com")
    with pytest.raises(HTTPException) as still_denied:
        require_internal_access(Request())
    assert still_denied.value.status_code == 403
    with get_db_connection() as connection:
        connection.execute("UPDATE users SET is_internal=1 WHERE id=1")
    assert require_internal_access(Request())["email"] == "operator@example.com"
