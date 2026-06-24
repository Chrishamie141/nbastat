import json
from models import DifficultyLevel, Parlay, ParlayLeg, ParlayResult, SportType
from nfl_parlay_grader import grade_nfl_parlays
from prediction_storage import load_parlay_history, save_parlay_result


def _save_result(db_file):
    result = ParlayResult(
        parlay=Parlay(
            sport=SportType.NFL,
            difficulty=DifficultyLevel.SAFE,
            legs=[
                ParlayLeg(
                    sport=SportType.NFL,
                    player="Patrick Mahomes",
                    team="KC",
                    stat_type="PASS_YDS",
                    line=249.5,
                    odds=-110,
                    prediction="Patrick Mahomes over 249.5 PASS_YDS",
                    confidence=70,
                ),
                ParlayLeg(
                    sport=SportType.NFL,
                    player="Josh Allen",
                    team="BUF",
                    stat_type="PASS_INT",
                    line=0.5,
                    odds=-110,
                    prediction="Josh Allen under 0.5 PASS_INT",
                    confidence=65,
                ),
            ],
        )
    )
    return save_parlay_result(result, db_file=db_file)


def test_grade_nfl_parlays_updates_leg_and_overall_results(tmp_path, monkeypatch):
    db_file = tmp_path / "predictions.db"
    parlay_id = _save_result(db_file)
    monkeypatch.setattr(
        "nfl_parlay_grader.get_nfl_final_player_stats",
        lambda: {"Patrick Mahomes": {"PASS_YDS": 275}, "Josh Allen": {"PASS_INT": 0}},
    )
    monkeypatch.setattr("nfl_parlay_grader.get_nfl_final_team_results", lambda: {})

    summaries = grade_nfl_parlays(db_file=db_file)
    rows = load_parlay_history(sport="NFL", db_file=db_file)
    legs = json.loads(rows[0]["legs_json"])

    assert summaries == [{"id": parlay_id, "difficulty": "SAFE", "hit": 2, "missed": 0, "pending": 0, "result_status": "hit"}]
    assert rows[0]["result_status"] == "hit"
    assert [leg["result"] for leg in legs] == ["hit", "hit"]


def test_grade_nfl_parlays_leaves_pending_when_finals_unavailable(tmp_path, monkeypatch):
    db_file = tmp_path / "predictions.db"
    _save_result(db_file)
    monkeypatch.setattr("nfl_parlay_grader.get_nfl_final_player_stats", lambda: {})
    monkeypatch.setattr("nfl_parlay_grader.get_nfl_final_team_results", lambda: {})

    summaries = grade_nfl_parlays(db_file=db_file)
    rows = load_parlay_history(sport="NFL", db_file=db_file)

    assert summaries == []
    assert rows[0]["result_status"] == "pending"
