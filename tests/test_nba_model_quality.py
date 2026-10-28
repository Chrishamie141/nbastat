from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from backtesting.nba_model_quality import (
    BASELINE_VERSION, CANDIDATE_VERSION, build_pregame_rows, evaluate,
)
from backtesting.reproduce_nba_model_quality import reproduce


def games():
    rows = []
    teams = ["A", "B", "C", "D"]
    for season_index, season in enumerate(("2023-24", "2024-25", "2025-26")):
        start = datetime(2023 + season_index, 10, 20, tzinfo=timezone.utc)
        for index in range(40):
            home = teams[index % 4]
            away = teams[(index + 1 + index // 4) % 4]
            if home == away:
                away = teams[(teams.index(home) + 1) % 4]
            # A learnable home/form signal with enough class variation.
            home_score = 108 + (index % 9) + (4 if home in {"A", "B"} else 0)
            away_score = 106 + ((index * 3) % 11) + (3 if away == "A" else 0)
            rows.append({
                "game_id": f"{season}-{index}", "season": season,
                "kickoff_time": (start + timedelta(days=index)).isoformat().replace("+00:00", "Z"),
                "home_team": home, "away_team": away,
                "home_score": home_score, "away_score": away_score,
            })
    return rows


def test_features_are_strictly_pregame_and_current_score_cannot_leak():
    original = games()
    changed = deepcopy(original)
    changed[8]["home_score"] += 1000
    first = build_pregame_rows(original)
    second = build_pregame_rows(changed)
    assert first[8] == second[8]
    for row in first:
        if row["feature_cutoff"]:
            assert row["feature_cutoff"] < row["kickoff_time"]


def test_evaluation_uses_frozen_season_split_and_stays_research_only():
    report = evaluate(games(), train_season="2023-24", validation_season="2024-25",
                      test_season="2025-26")
    assert report["policy"]["status"] == "RESEARCH_ONLY"
    assert report["policy"]["productionModelChanged"] is False
    assert report["policy"]["heldOutTestUsedForSelection"] is False
    assert report["baseline"]["modelVersion"] == BASELINE_VERSION
    assert report["candidate"]["modelVersion"] == CANDIDATE_VERSION
    assert report["baseline"]["games"] == report["candidate"]["games"] == 40
    for model in ("baseline", "candidate"):
        assert 0 <= report[model]["accuracy"] <= 1
        assert 0 <= report[model]["brierScore"] <= 1
        assert report[model]["logLoss"] >= 0


def test_independent_reproduction_audits_split_leakage_and_agrees():
    source = games()
    for row in source:
        if row["home_score"] == row["away_score"]:
            row["home_score"] += 1
    import hashlib
    import json
    dataset = {
        "metadata": {
            "gameCount": len(source),
            "gamesSha256": hashlib.sha256(
                json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        },
        "games": source,
    }
    reference = evaluate(source, train_season="2023-24", validation_season="2024-25",
                         test_season="2025-26")
    report, artifact = reproduce(dataset, reference)
    assert report["checks"]["featureCutoffViolations"] == 0
    assert report["checks"]["splitGameOverlap"] == 0
    assert report["checks"]["strictSeasonOrdering"] is True
    assert report["referenceAgreement"]["gameIdsMatch"] is True
    assert report["referenceAgreement"]["maximumCandidateProbabilityDelta"] < 1e-12
    assert artifact["status"] == "RESEARCH_ONLY"
    assert artifact["wageringAuthorized"] is False
