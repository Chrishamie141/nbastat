"""NFL parlay performance reporting from saved parlay history."""

from __future__ import annotations

import json
from collections import defaultdict

from prediction_storage import DB_FILE, get_connection, initialize_parlay_history

TRACKED_PROP_TYPES = [
    "PASS_YDS",
    "RUSH_YDS",
    "REC_YDS",
    "RECEPTIONS",
    "ANYTIME_TD",
    "PASS_TDS",
    "PASS_INT",
]

PROP_TYPE_ALIASES = {
    "PASSING_YARDS": "PASS_YDS",
    "RUSHING_YARDS": "RUSH_YDS",
    "RECEIVING_YARDS": "REC_YDS",
    "TD": "ANYTIME_TD",
    "PASS_TD": "PASS_TDS",
    "PASSING_TDS": "PASS_TDS",
    "PASSING_TOUCHDOWNS": "PASS_TDS",
    "PASSING_INTERCEPTIONS": "PASS_INT",
}

NOT_ENOUGH_NFL_HISTORY_MESSAGE = (
    "Not enough graded NFL history yet. Generate parlays and grade them after games finish."
)


def normalize_prop_type(stat_type):
    """Normalize NFL prop stat names used across builder/grader versions."""
    key = str(stat_type or "").strip().upper().replace(" ", "_")
    return PROP_TYPE_ALIASES.get(key, key)


def load_nfl_parlay_history(db_file=DB_FILE):
    """Load all saved NFL parlay rows from predictions.db."""
    initialize_parlay_history(db_file)
    with get_connection(db_file) as conn:
        rows = conn.execute(
            """
            SELECT * FROM parlay_history
            WHERE UPPER(sport) = 'NFL'
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _safe_load_legs(row):
    try:
        legs = json.loads(row.get("legs_json") or "[]")
    except json.JSONDecodeError:
        return []
    return legs if isinstance(legs, list) else []


def _leg_result(leg):
    result = str(leg.get("result") or "").strip().lower()
    if result in {"hit", "missed", "pending"}:
        return result
    return "pending"


def _rate(hit_count, total_count):
    return hit_count / total_count if total_count else None


def calculate_nfl_performance(rows):
    """Calculate NFL performance metrics from raw parlay_history rows."""
    total_parlays = len(rows)
    pending_parlays = sum(
        1 for row in rows if str(row.get("result_status") or "").lower() == "pending"
    )
    graded_rows = [
        row for row in rows if str(row.get("result_status") or "").lower() in {"hit", "missed"}
    ]

    by_difficulty = defaultdict(lambda: {"hits": 0, "total": 0})
    leg_by_prop = {prop_type: {"hits": 0, "total": 0} for prop_type in TRACKED_PROP_TYPES}
    hit_confidences = []
    missed_confidences = []

    for row in graded_rows:
        difficulty = str(row.get("difficulty") or "UNKNOWN").upper()
        by_difficulty[difficulty]["total"] += 1
        if str(row.get("result_status") or "").lower() == "hit":
            by_difficulty[difficulty]["hits"] += 1

        for leg in _safe_load_legs(row):
            result = _leg_result(leg)
            if result not in {"hit", "missed"}:
                continue
            prop_type = normalize_prop_type(leg.get("stat_type") or leg.get("market"))
            if prop_type not in leg_by_prop:
                continue
            leg_by_prop[prop_type]["total"] += 1
            if result == "hit":
                leg_by_prop[prop_type]["hits"] += 1
            confidence = leg.get("confidence")
            if confidence is not None:
                try:
                    confidence_bucket = hit_confidences if result == "hit" else missed_confidences
                    confidence_bucket.append(float(confidence))
                except (TypeError, ValueError):
                    pass

    prop_rates = {
        prop_type: {**counts, "hit_rate": _rate(counts["hits"], counts["total"])}
        for prop_type, counts in leg_by_prop.items()
    }
    ranked_props = [
        (prop_type, data)
        for prop_type, data in prop_rates.items()
        if data["total"] > 0
    ]
    ranked_props.sort(
        key=lambda item: (item[1]["hit_rate"], item[1]["total"], item[0]), reverse=True
    )

    return {
        "total_parlays": total_parlays,
        "pending_parlays": pending_parlays,
        "graded_parlays": len(graded_rows),
        "hit_rate_by_difficulty": {
            difficulty: {**counts, "hit_rate": _rate(counts["hits"], counts["total"])}
            for difficulty, counts in sorted(by_difficulty.items())
        },
        "leg_hit_rate_by_prop_type": prop_rates,
        "avg_confidence_hit_legs": (
            sum(hit_confidences) / len(hit_confidences) if hit_confidences else None
        ),
        "avg_confidence_missed_legs": (
            sum(missed_confidences) / len(missed_confidences) if missed_confidences else None
        ),
        "best_prop_type": ranked_props[0][0] if ranked_props else None,
        "worst_prop_type": (
            sorted(ranked_props, key=lambda item: (item[1]["hit_rate"], -item[1]["total"], item[0]))[0][0]
            if ranked_props
            else None
        ),
    }


def _format_rate(value):
    return "N/A" if value is None else f"{value * 100:.1f}%"


def _format_confidence(value):
    return "N/A" if value is None else f"{value:.1f}%"


def print_nfl_performance_report(db_file=DB_FILE):
    """Print a clean CLI report for graded NFL parlay performance."""
    report = calculate_nfl_performance(load_nfl_parlay_history(db_file=db_file))
    print("\n========================")
    print("NFL PERFORMANCE REPORT")
    print("======================")
    if report["graded_parlays"] == 0:
        print(NOT_ENOUGH_NFL_HISTORY_MESSAGE)
        return report

    print(f"Total Parlays: {report['total_parlays']}")
    print(f"Pending Parlays: {report['pending_parlays']}")
    print(f"Graded Parlays: {report['graded_parlays']}")

    print("\nHit Rate by Difficulty:")
    for difficulty in ("SAFE", "BALANCED", "AGGRESSIVE"):
        data = report["hit_rate_by_difficulty"].get(
            difficulty, {"hits": 0, "total": 0, "hit_rate": None}
        )
        print(
            f"- {difficulty.title()}: {_format_rate(data['hit_rate'])} "
            f"({data['hits']}/{data['total']})"
        )

    print("\nLeg Hit Rate by Prop Type:")
    for prop_type in TRACKED_PROP_TYPES:
        data = report["leg_hit_rate_by_prop_type"][prop_type]
        print(f"- {prop_type}: {_format_rate(data['hit_rate'])} ({data['hits']}/{data['total']})")

    print("\nConfidence Split:")
    print(
        f"- Average confidence of hit legs: "
        f"{_format_confidence(report['avg_confidence_hit_legs'])}"
    )
    print(
        f"- Average confidence of missed legs: "
        f"{_format_confidence(report['avg_confidence_missed_legs'])}"
    )
    print(f"\nBest-performing prop type: {report['best_prop_type'] or 'N/A'}")
    print(f"Worst-performing prop type: {report['worst_prop_type'] or 'N/A'}")
    return report
