"""Build the reviewed Data report snapshot from reproduced analysis outputs."""

from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def csv_rows(name: str) -> list[dict]:
    with (DATA / name).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    numeric = {
        "games", "accuracy", "brierScore", "logLoss", "auc",
        "expectedCalibrationError", "homePickPct", "homePickCount", "awayPickCount",
        "accuracy_ci95_low", "accuracy_ci95_high", "elo_rating", "eligible_final_games",
        "home_rating", "away_rating", "home_minus_away_rating", "home_court_adjustment",
        "adjusted_home_rating_difference", "home_win_probability", "away_win_probability",
        "evidence_games", "lower", "upper", "count", "averageHomeProbability",
        "actualHomeWinRate",
    }
    for row in rows:
        for key in numeric & row.keys():
            if row[key] not in {"", None}:
                row[key] = int(row[key]) if key in {
                    "games", "homePickCount", "awayPickCount", "eligible_final_games",
                    "evidence_games", "count",
                } else float(row[key])
            else:
                row[key] = None
    return rows


def source(file: str, grain: str, methods: list[dict], definitions: dict) -> dict:
    return {
        "provider": "SmartBetSports repository research artifact",
        "files": [file],
        "sourceFiles": [file],
        "freshness": "Frozen 2026-10-29 analysis cutoff",
        "grain": grain,
        "metricDefinitions": definitions,
        "evidenceFlow": [
            {
                "title": "Frozen source",
                "detail": "Read the named local artifact without mutating application or production data.",
            },
            *methods,
        ],
    }


def main() -> None:
    comparison = csv_rows("model-comparison.csv")
    ratings = csv_rows("current-team-ratings.csv")
    forecasts = csv_rows("upcoming-forecast-diagnostics.csv")
    season_metrics = csv_rows("season-metrics.csv")
    calibration = csv_rows("calibration-buckets.csv")
    current = json.loads((DATA / "current-season-diagnosis.json").read_text(encoding="utf-8"))
    snapshot = {
        "title": "NBA Algorithm Diagnosis",
        "description": "Why the current Elo selects home teams and which research model is strongest.",
        "status": "reviewed",
        "generatedAt": "2026-10-29T23:59:00Z",
        "report": {"asOf": "2026-10-29"},
        "filters": [],
        "exportPreferences": {"selected": None, "destinations": []},
        "queries": {
            "model_comparison": {
                "rows": comparison,
                "source": source(
                    "data/model-comparison.csv",
                    "one row per evaluated model on the untouched 2025-26 test season",
                    [
                        {"title": "Chronological split", "detail": "Train 2023-24, select on 2024-25, and evaluate once on untouched 2025-26."},
                        {"title": "Probability metrics", "detail": "Compute accuracy, Brier score, log loss, AUC, ten-bin expected calibration error, and selection distribution on identical games."},
                    ],
                    {
                        "accuracy": {"label": "Accuracy", "definition": "Correct winner selections divided by evaluated games.", "componentIds": ["model-table", "accuracy-chart"]},
                        "brierScore": {"label": "Brier score", "definition": "Mean squared error of predicted home-win probability; lower is better.", "componentIds": ["model-table", "brier-chart"]},
                        "logLoss": {"label": "Log loss", "definition": "Mean binary cross-entropy of predicted home-win probability; lower is better.", "componentIds": ["model-table"]},
                        "homePickPct": {"label": "Home pick rate", "definition": "Share of games whose predicted home-win probability is at least 0.5.", "componentIds": ["model-table", "home-pick-chart"]},
                    },
                ),
            },
            "current_ratings": {
                "rows": ratings,
                "source": source(
                    "data/current-team-ratings.csv",
                    "one row per NBA team after 68 eligible 2026-27 finals",
                    [{"title": "Exact Elo replay", "detail": "Initialize each team at 1500 and apply K=20 chronological updates with a 65-point home adjustment."}],
                    {
                        "elo_rating": {"label": "Current Elo rating", "definition": "Rating after all 68 eligible regular-season finals in the frozen production cutoff.", "componentIds": ["ratings-table"]},
                    },
                ),
            },
            "forecast_diagnostics": {
                "rows": forecasts,
                "source": source(
                    "data/upcoming-forecast-diagnostics.csv",
                    "one row per upcoming baseline forecast in the 2026-10-29 production population",
                    [{"title": "Forecast reconstruction", "detail": "Apply the exact production probability formula to the frozen team ratings for all 102 scheduled games."}],
                    {
                        "adjusted_home_rating_difference": {"label": "Adjusted home rating difference", "definition": "Home Elo minus away Elo plus the fixed 65-point home adjustment.", "componentIds": ["forecasts-table"]},
                        "home_win_probability": {"label": "Home win probability", "definition": "Production Elo conversion of the adjusted rating difference.", "componentIds": ["forecasts-table"]},
                    },
                ),
            },
            "season_metrics": {
                "rows": season_metrics,
                "source": source(
                    "data/season-metrics.csv",
                    "one row per model and chronological evaluation season",
                    [{"title": "Season stability", "detail": "Keep 2024-25 labeled as validation/model-selection and 2025-26 as untouched test evidence."}],
                    {},
                ),
            },
            "calibration": {
                "rows": calibration,
                "source": source(
                    "data/calibration-buckets.csv",
                    "one row per model and predicted home-probability decile on 2025-26",
                    [{"title": "Calibration", "detail": "Compare average predicted home probability with actual home win rate within each decile."}],
                    {},
                ),
            },
            "current_summary": {
                "rows": [{
                    "regularSeasonFinals": current["checks"]["regularSeasonFinals"],
                    "upcomingForecasts": current["checks"]["upcomingForecasts"],
                    "homeSelections": current["checks"]["homeSelections"],
                    "awaySelections": current["checks"]["awaySelections"],
                    "candidateHomeSelections": current["checks"]["researchCandidateHomeSelections"],
                    "candidateAwaySelections": current["checks"]["researchCandidateAwaySelections"],
                    "equalRatingHomeProbability": current["checks"]["equalRatingHomeProbability"],
                    "minimumRating": current["checks"]["ratingRange"][0],
                    "maximumRating": current["checks"]["ratingRange"][1],
                }],
                "source": source(
                    "data/current-season-diagnosis.json",
                    "single frozen current-season diagnostic summary",
                    [{"title": "Current population", "detail": "Reconcile 237 schedule records to 68 finals and 102 upcoming forecasts."}],
                    {},
                ),
            },
        },
    }
    (ROOT / "report-snapshot.json").write_text(
        json.dumps(snapshot, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
