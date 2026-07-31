import hashlib
import json

import numpy as np
import pytest

from backtesting.build_nfl_player_prop_predictions import (
    collapse_opportunities, opportunity_key, persist_week, probabilities,
)


def _quote(**changes):
    row = {"season": 2025, "week": 1, "game_id": "g", "canonical_player_id": "p1",
           "player_name": "Player", "team": "KC", "market": "receptions", "line": 4.0,
           "selection": "OVER", "bookmaker": "a"}
    row.update(changes)
    return row


def test_duplicate_books_collapse_and_outcomes_are_not_copied():
    rows = [_quote(bookmaker="a", outcome=99, grade="WIN"),
            _quote(bookmaker="b", outcome=0, grade="LOSS")]
    collapsed = collapse_opportunities(rows)
    assert len(collapsed) == 1
    assert opportunity_key(collapsed[0]) == (2025, 1, "g", "p1", "receptions", 4.0, "OVER")
    assert "outcome" not in collapsed[0] and "grade" not in collapsed[0]


def test_lines_sides_and_discrete_push_probability():
    draws = np.array([3, 4, 4, 5])
    integer = probabilities(draws, 4)
    assert integer == {"OVER": .25, "UNDER": .25, "PUSH": .5}
    assert sum(integer.values()) == pytest.approx(1)
    half = probabilities(draws, 4.5)
    assert half == {"OVER": .25, "UNDER": .75, "PUSH": 0.0}
    assert probabilities(draws, 3.5)["OVER"] > half["OVER"]


def test_persistence_is_deterministic_and_manifest_checksum(tmp_path):
    directory = tmp_path / "nfl" / "2025" / "week_01"
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps({"datasets": {"games": {"records": 1}}}))
    row = {"season": 2025, "week": 1, "game_id": "g", "canonical_player_id": "p1",
           "player_name": "Player", "team": "KC", "market": "receptions", "line": 4.0,
           "side": "OVER", "model_probability": .25, "push_probability": .5,
           "over_probability": .25, "under_probability": .25,
           "model_version": "nfl_game_baseline_v1", "prediction_cutoff": "2025-09-01T00:00:00Z",
           "generated_at": "2025-09-01T00:00:00Z", "seed": 7, "simulations": 4,
           "readiness": "READY", "provenance": {"outcome_inputs": [], "network_contacted": False}}
    diagnostics = {"readiness_counts": {"READY": 1}}
    persist_week(tmp_path, 2025, 1, [row], diagnostics,
                 model_version="nfl_game_baseline_v1", seed=7, simulations=4, overwrite=False)
    artifact = directory / "player_prop_predictions.json"
    before = artifact.read_bytes()
    persist_week(tmp_path, 2025, 1, [row], diagnostics,
                 model_version="nfl_game_baseline_v1", seed=7, simulations=4, overwrite=True)
    assert artifact.read_bytes() == before
    manifest = json.loads((directory / "manifest.json").read_text())
    entry = manifest["datasets"]["player_prop_predictions"]
    assert entry["sha256"] == hashlib.sha256(before).hexdigest()
    assert manifest["datasets"]["games"] == {"records": 1}
    assert manifest["source_lineage"]["player_prop_predictions"]["network_contacted"] is False
