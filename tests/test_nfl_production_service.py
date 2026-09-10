from __future__ import annotations

from datetime import datetime, timezone
import json

from backend.app.database import get_db_connection
from backend.app.services import nfl_product_service
from backend.app.services.nfl_production_service import (
    audit_week, benchmark_performance, capture_prediction_history, generate_benchmarks,
    grade_benchmarks, grade_user_parlays, initialize, persist_final, prediction_history, settle_predictions,
    void_unverifiable_benchmarks,
)
from backend.app.services.parlay_history_service import load_web_parlays, save_web_parlay
from models import DifficultyLevel, Parlay, ParlayLeg, ParlayResult, SportType
from nfl_parlay_grader import _grade_player_leg, _grade_team_leg, _overall_status


def game(status="scheduled", kickoff="2099-09-10T00:20:00Z"):
    return {"game_id": "espn-401872656", "id": "espn-401872656", "season": 2026,
            "season_type": "regular", "week": 1, "display_week": 1, "kickoff_time": kickoff,
            "away_team": "NE", "home_team": "SEA", "away_score": 10 if status == "final" else None,
            "home_score": 13 if status == "final" else None, "status": status, "provider": "espn"}


def seed_prediction(generated="2026-09-09T12:00:00Z", winner="SEA", user_id=7):
    nfl_product_service._initialize_predictions.cache_clear()
    nfl_product_service._initialize_predictions()
    payload = {"winner": winner, "winProbability": .61, "edge": .07, "modelVersion": "nfl-v-test",
               "dataAsOf": "2026-09-09T11:00:00Z", "market": {"homeOdds": -120, "awayOdds": 110}}
    with get_db_connection() as connection:
        row = connection.execute("""INSERT INTO nfl_game_predictions
            (user_id,game_id,season,week,kickoff_time,generated_at,model_version,prediction_json,
             season_type,display_week,provider_week,provider,week_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
            (user_id, "espn-401872656", 2026, 1, "2099-09-10T00:20:00Z", generated,
             "nfl-v-test", json.dumps(payload), "regular", 1, 1, "espn", "REG1")).fetchone()
    return row["id"], payload


def final_detail(player_yards=250):
    return {"game": {"id": "401872656", "season": 2026, "seasonPhase": "regular_season", "week": 1,
                     "startTimeUtc": "2099-09-10T00:20:00Z", "status": "final", "statusUpdatedAt": "2099-09-10T04:00:00Z",
                     "awayTeam": {"abbreviation": "NE"}, "homeTeam": {"abbreviation": "SEA"},
                     "awayScore": 10, "homeScore": 13},
            "actuals": {"teamStats": [{"team": "SEA", "statistics": [{"name": "Total Yards", "value": 320}]}],
                        "playerGroups": [{"group": "passing", "items": [{"playerName": "Sam Test", "stats": {"YDS": player_yards, "TD": 2, "INT": 0}}]}]}}


def fake_builder(profile, **_kwargs):
    legs = [ParlayLeg(sport=SportType.NFL, player="Sam Test", team="SEA", stat_type="PASS_YDS",
                      line=250, odds=-110, prediction="Sam Test over 250 PASS_YDS", confidence=65,
                      provider="the-odds-api", bookmaker="Test Book", event_id="event-1",
                      market_timestamp="2026-09-09T20:00:00Z", market_kickoff="2099-09-10T00:20:00Z")]
    return ParlayResult(parlay=Parlay(sport=SportType.NFL, difficulty=DifficultyLevel.from_input(profile), legs=legs),
                        estimated_odds=-110, combined_probability=.65, notes="verified")


def test_prediction_history_settles_original_snapshot_idempotently():
    prediction_id, original = seed_prediction()
    captured = capture_prediction_history(season=2026, season_type="regular", week=1, schedule=[game()])
    assert captured["inserted"] == 1
    assert capture_prediction_history(season=2026, season_type="regular", week=1, schedule=[game()])["inserted"] == 0
    assert persist_final(final_detail()) is True
    assert persist_final(final_detail()) is False
    assert settle_predictions(season=2026, season_type="regular", week=1)["settled"] == 1
    assert settle_predictions(season=2026, season_type="regular", week=1)["settled"] == 0
    rows = prediction_history(user_id=7)
    assert rows[0]["id"] == prediction_id and rows[0]["resultStatus"] == "WON"
    assert rows[0]["awayScore"] == 10 and rows[0]["homeScore"] == 13
    with get_db_connection() as connection:
        frozen = json.loads(connection.execute("SELECT prediction_json FROM nfl_game_predictions WHERE id=?", (prediction_id,)).fetchone()["prediction_json"])
    assert frozen == original


def test_post_kickoff_prediction_is_rejected_from_history():
    seed_prediction(generated="2099-09-10T00:20:00Z")
    result = capture_prediction_history(season=2026, season_type="regular", week=1, schedule=[game()])
    assert result["inserted"] == 0
    assert result["rejected"][0]["reason"] == "generated_at_or_after_kickoff"


def test_grading_push_void_and_pending_rules():
    assert _grade_player_leg({"player": "A", "stat_type": "PASS_YDS", "line": 250, "side": "over"}, {"A": {"PASS_YDS": 250}}) == "push"
    assert _grade_team_leg({"team": "SEA", "stat_type": "SPREAD", "line": -3}, {"SEA": {"margin": 3}}) == "push"
    assert _grade_team_leg({"team": "SEA", "stat_type": "TOTAL", "line": 23, "side": "over"}, {"SEA": {"total": 23}}) == "push"
    assert _overall_status(["hit", "push"]) == "hit"
    assert _overall_status(["void", "void"]) == "void"
    assert _overall_status(["hit", "pending"]) == "pending"
    assert _overall_status(["hit", "missed", "push"]) == "missed"


def test_benchmarks_freeze_all_profiles_and_grade_push_without_duplication():
    seed_prediction()
    first = generate_benchmarks(season=2026, season_type="regular", week=1, schedule=[game()],
                                clock=lambda: datetime(2026, 9, 9, tzinfo=timezone.utc), parlay_builder=fake_builder)
    assert first == {"created": 3, "noBet": 0, "retryableSkipped": 0}
    second = generate_benchmarks(season=2026, season_type="regular", week=1, schedule=[game()],
                                 clock=lambda: datetime(2026, 9, 9, tzinfo=timezone.utc), parlay_builder=fake_builder)
    assert second["created"] == 0
    persist_final(final_detail(player_yards=250))
    result = grade_benchmarks(season=2026, season_type="regular", week=1)
    assert result["graded"] == 3
    performance = benchmark_performance(season=2026, season_type="regular", week=1)
    assert performance["overall"]["pushVoid"] == 3
    assert all(performance["byProfile"][profile]["generated"] == 1 for profile in ("SAFE", "BALANCED", "AGGRESSIVE"))


def test_benchmark_rejects_market_timestamp_at_kickoff():
    seed_prediction()
    def boundary_builder(profile, **kwargs):
        ticket = fake_builder(profile, **kwargs)
        leg = ticket.parlay.legs[0]
        replacement = ParlayLeg(**{**leg.__dict__, "market_timestamp": "2099-09-10T00:20:00Z"})
        return ParlayResult(parlay=Parlay(sport=SportType.NFL, difficulty=DifficultyLevel.from_input(profile), legs=[replacement]), notes="boundary")
    result = generate_benchmarks(season=2026, season_type="regular", week=1, schedule=[game()],
                                 clock=lambda: datetime(2026, 9, 9, tzinfo=timezone.utc), parlay_builder=boundary_builder)
    assert result["created"] == 3 and result["noBet"] == 3
    assert benchmark_performance(season=2026, season_type="regular", week=1)["overall"]["noBet"] == 3


def test_unverifiable_frozen_ticket_is_voided_without_deleting_legs():
    seed_prediction()
    initialize()
    with get_db_connection() as connection:
        benchmark = connection.execute("""INSERT INTO nfl_sgp_benchmarks
            (benchmark_key,game_id,season,season_type,week,profile,generated_at,kickoff_time,model_version,
             source_hash,ticket_status,legs_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
            ("legacy", "espn-401872656", 2026, "regular", 1, "SAFE", "2026-09-09T12:00:00Z",
             "2099-09-10T00:20:00Z", "v1", "hash", "PENDING", "[{}]" )).fetchone()
        connection.execute("""INSERT INTO nfl_sgp_benchmark_legs
            (benchmark_id,leg_index,market_type,metadata_json,result_status) VALUES (?,?,?,?,?)""",
            (benchmark["id"], 0, "PASS_YDS", "{}", "PENDING"))
    assert void_unverifiable_benchmarks(season=2026, season_type="regular", week=1)["voided"] == 1
    with get_db_connection() as connection:
        ticket = connection.execute("SELECT ticket_status,no_bet_reason FROM nfl_sgp_benchmarks WHERE benchmark_key='legacy'").fetchone()
        leg = connection.execute("SELECT result_status FROM nfl_sgp_benchmark_legs WHERE benchmark_id=?", (benchmark["id"],)).fetchone()
    assert ticket["ticket_status"] == "VOID" and ticket["no_bet_reason"] == "INCOMPLETE_FROZEN_MARKET_PROVENANCE"
    assert leg["result_status"] == "VOID"


def test_no_bet_is_persisted_and_audit_is_deterministic():
    seed_prediction()
    def no_bet(profile, **_kwargs):
        return ParlayResult(parlay=Parlay(sport=SportType.NFL, difficulty=DifficultyLevel.from_input(profile), legs=[]), notes="No verified props")
    generated = generate_benchmarks(season=2026, season_type="regular", week=1, schedule=[game()],
                                    clock=lambda: datetime(2026, 9, 9, tzinfo=timezone.utc), parlay_builder=no_bet)
    assert generated["noBet"] == 3
    capture_prediction_history(season=2026, season_type="regular", week=1, schedule=[game()])
    first = audit_week(season=2026, season_type="regular", week=1, schedule=[game()], persist=False)
    second = audit_week(season=2026, season_type="regular", week=1, schedule=[game()], persist=False)
    assert first["result"] == second["result"] == "PASS"
    assert first["counts"] == second["counts"]


def test_pending_benchmarks_never_claim_reportable_sample():
    seed_prediction()
    generate_benchmarks(season=2026, season_type="regular", week=1, schedule=[game()],
                        clock=lambda: datetime(2026, 9, 9, tzinfo=timezone.utc), parlay_builder=fake_builder)
    performance = benchmark_performance(season=2026, season_type="regular", week=1)
    assert performance["overall"]["pending"] == 3
    assert performance["sampleStatus"] == "INSUFFICIENT_SAMPLE"


def test_vercel_audit_does_not_require_frontend_source_in_api_bundle(monkeypatch):
    seed_prediction()
    capture_prediction_history(season=2026, season_type="regular", week=1, schedule=[game()])
    monkeypatch.setattr("backend.app.services.nfl_production_service.initialize", lambda: None)
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://smartbetsports.com")
    monkeypatch.setattr("backend.app.services.nfl_production_service.Path.exists", lambda _path: False)
    report = audit_week(season=2026, season_type="regular", week=1, schedule=[game()], persist=False)
    frontend = next(check for check in report["checks"] if check["name"] == "Frontend smoke tests")
    assert frontend["status"] == "PASS"
    assert "split Vercel frontend deployment" in frontend["detail"]


def test_user_parlay_settles_only_with_persisted_game_identity():
    ticket = fake_builder("SAFE")
    save_web_parlay(ticket, user_id=9, model_version="nfl-v-test",
                    context={"gameId": "espn-401872656", "season": 2026,
                             "seasonType": "regular", "week": 1,
                             "kickoffTime": "2099-09-10T00:20:00Z"})
    persist_final(final_detail(player_yards=275))
    result = grade_user_parlays(season=2026, season_type="regular", week=1)
    assert result["graded"] == 1
    row = load_web_parlays(user_id=9)[0]
    assert row["result_status"] == "WON"
    assert json.loads(row["legs_json"])[0]["result"] == "HIT"
