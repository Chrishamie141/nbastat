import json

from nfl_performance_report import (
    NOT_ENOUGH_NFL_HISTORY_MESSAGE,
    calculate_nfl_performance,
    print_nfl_performance_report,
)
from prediction_storage import get_connection, initialize_parlay_history


def _insert_parlay(db_file, difficulty, status, legs):
    initialize_parlay_history(db_file)
    with get_connection(db_file) as conn:
        conn.execute(
            """
            INSERT INTO parlay_history (
                sport, created_at, difficulty, legs_json, estimated_odds,
                combined_probability, result_status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("NFL", "2026-01-01T00:00:00+00:00", difficulty, json.dumps(legs), None, 0.5, status, None),
        )


def test_calculate_nfl_performance_metrics():
    rows = [
        {
            "difficulty": "SAFE",
            "result_status": "hit",
            "legs_json": json.dumps([
                {"stat_type": "PASS_YDS", "result": "hit", "confidence": 70},
                {"stat_type": "RUSH_YDS", "result": "hit", "confidence": 62},
            ]),
        },
        {
            "difficulty": "BALANCED",
            "result_status": "missed",
            "legs_json": json.dumps([
                {"stat_type": "PASS_TD", "result": "missed", "confidence": 55},
                {"stat_type": "PASS_INT", "result": "hit", "confidence": 61},
            ]),
        },
        {"difficulty": "AGGRESSIVE", "result_status": "pending", "legs_json": "[]"},
    ]

    report = calculate_nfl_performance(rows)

    assert report["total_parlays"] == 3
    assert report["pending_parlays"] == 1
    assert report["graded_parlays"] == 2
    assert report["hit_rate_by_difficulty"]["SAFE"]["hit_rate"] == 1.0
    assert report["hit_rate_by_difficulty"]["BALANCED"]["hit_rate"] == 0.0
    assert report["leg_hit_rate_by_prop_type"]["PASS_TDS"]["hit_rate"] == 0.0
    assert report["avg_confidence_hit_legs"] == (70 + 62 + 61) / 3
    assert report["avg_confidence_missed_legs"] == 55
    assert report["best_prop_type"] in {"PASS_YDS", "RUSH_YDS", "PASS_INT"}
    assert report["worst_prop_type"] == "PASS_TDS"


def test_print_nfl_performance_report_not_enough_history(tmp_path, capsys):
    db_file = tmp_path / "predictions.db"
    _insert_parlay(db_file, "SAFE", "pending", [])

    report = print_nfl_performance_report(db_file=db_file)
    output = capsys.readouterr().out

    assert report["graded_parlays"] == 0
    assert NOT_ENOUGH_NFL_HISTORY_MESSAGE in output
