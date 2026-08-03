from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtesting.forward_shadow_nfl_player_props import (
    _digest,
    build_decisions,
    freeze_policy,
    grade_batch,
    lock_batch,
    summarize,
    write_summary,
)


def _policy() -> dict:
    value = {"schema_version": 1, "policy_id": "shadow", "minimum_expected_value": .05}
    value["policy_fingerprint"] = _digest(value)
    return value


def _candidates() -> list[dict]:
    common = {"season": 2026, "week": 1, "game_id": "g1", "canonical_player_id": "p1",
              "market": "receptions", "line": 4.5, "bookmaker": "book",
              "quote_timestamp": "2026-09-01T10:00:00Z", "generated_at": "2026-09-01T10:01:00Z",
              "prediction_cutoff": "2026-09-01T11:00:00Z"}
    return [{**common, "side": "OVER", "policy_probability": .60, "decimal_odds": 2.0},
            {**common, "side": "UNDER", "policy_probability": .40, "decimal_odds": 2.0}]


def test_shadow_selects_at_most_one_side_and_preserves_candidates() -> None:
    rows = build_decisions(_candidates(), _policy(), locked_at="2026-09-01T10:05:00Z")
    assert len(rows) == 1 and rows[0]["decision"] == "BET"
    assert rows[0]["selected_side"] == "OVER"
    assert len(rows[0]["candidate_sides"]) == 2


def test_shadow_rejects_late_or_outcome_bearing_candidates() -> None:
    late = [{**row, "quote_timestamp": "2026-09-01T11:00:01Z"} for row in _candidates()]
    with pytest.raises(ValueError, match="after prediction cutoff"):
        build_decisions(late, _policy(), locked_at="2026-09-01T10:05:00Z")
    contaminated = [{**row, "actual": 5} for row in _candidates()]
    with pytest.raises(ValueError, match="outcome-bearing"):
        build_decisions(contaminated, _policy(), locked_at="2026-09-01T10:05:00Z")


def test_append_only_ledger_is_idempotent_and_rejects_changed_decision(tmp_path: Path) -> None:
    candidates, policy = tmp_path / "candidates.json", tmp_path / "policy.json"
    candidates.write_text(json.dumps(_candidates()), encoding="utf-8")
    policy.write_text(json.dumps(_policy()), encoding="utf-8")
    first = lock_batch(candidates, policy, tmp_path / "ledger", locked_at="2026-09-01T10:05:00Z")
    second = lock_batch(candidates, policy, tmp_path / "ledger", locked_at="2026-09-01T10:05:00Z")
    assert first["entry_path"] == second["entry_path"]
    changed = _candidates(); changed[0]["policy_probability"] = .55
    candidates.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="different decision"):
        lock_batch(candidates, policy, tmp_path / "ledger", locked_at="2026-09-01T10:05:00Z")


def test_grading_is_separate_and_never_mutates_locked_entry(tmp_path: Path) -> None:
    candidates, policy = tmp_path / "candidates.json", tmp_path / "policy.json"
    candidates.write_text(json.dumps(_candidates()), encoding="utf-8")
    policy.write_text(json.dumps(_policy()), encoding="utf-8")
    locked = lock_batch(candidates, policy, tmp_path / "ledger", locked_at="2026-09-01T10:05:00Z")
    entry = Path(locked["entry_path"]); before = entry.read_bytes()
    outcomes = tmp_path / "outcomes.json"
    outcomes.write_text(json.dumps([{"game_id": "g1", "canonical_player_id": "p1",
        "record_role": "game_outcome", "is_pregame": False, "stats": {"receptions": 6}}]), encoding="utf-8")
    report = grade_batch(entry, outcomes, tmp_path / "ledger" / "grades")
    assert report["grades"][0]["grade"] == "WIN"
    assert entry.read_bytes() == before
    summary = summarize(tmp_path / "ledger")
    assert summary["bets"] == 1 and summary["wins"] == 1
    assert summary["profit_units"] == pytest.approx(1.0)


def test_pending_grade_can_be_followed_by_append_only_settled_grade(tmp_path: Path) -> None:
    candidates, policy = tmp_path / "candidates.json", tmp_path / "policy.json"
    candidates.write_text(json.dumps(_candidates()), encoding="utf-8")
    policy.write_text(json.dumps(_policy()), encoding="utf-8")
    locked = lock_batch(candidates, policy, tmp_path / "ledger", locked_at="2026-09-01T10:05:00Z")
    entry = Path(locked["entry_path"])
    pending = tmp_path / "pending.json"; pending.write_text("[]", encoding="utf-8")
    grade_batch(entry, pending, tmp_path / "ledger" / "grades")
    settled = tmp_path / "settled.json"
    settled.write_text(json.dumps([{"game_id": "g1", "canonical_player_id": "p1",
        "record_role": "game_outcome", "is_pregame": False, "completed_at": "2026-09-01T15:00:00Z",
        "stats": {"receptions": 2}}]), encoding="utf-8")
    grade_batch(entry, settled, tmp_path / "ledger" / "grades")
    summary = summarize(tmp_path / "ledger")
    assert summary["pending_bets"] == 0 and summary["losses"] == 1
    assert len(list((tmp_path / "ledger" / "grades").glob("*.json"))) == 2


def test_policy_freeze_uses_latest_pre_outcome_selected_fold(tmp_path: Path) -> None:
    report = tmp_path / "report.json"; manifest = tmp_path / "manifest.json"; output = tmp_path / "policy.json"
    report.write_text(json.dumps({"folds": [
        {"test_season": 2025, "test_week": 17, "selected_configuration": "a",
         "selected_threshold": .02, "inner_periods": [[2025, 9]]},
        {"test_season": 2025, "test_week": 18, "selected_configuration": "b",
         "selected_threshold": .05, "inner_periods": [[2025, 10]]},
    ]}), encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    policy = freeze_policy(report, manifest, output)
    assert policy["configuration"] == "b" and policy["minimum_expected_value"] == .05
    assert freeze_policy(report, manifest, output) == policy


def test_empty_shadow_summary_and_manifest_are_deterministic(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"; ledger.mkdir()
    (ledger / "frozen_policy.json").write_text(json.dumps(_policy()), encoding="utf-8")
    output = ledger / "shadow_summary.json"
    first = write_summary(ledger, output)
    first_bytes = (ledger / "shadow_manifest.json").read_bytes()
    second = write_summary(ledger, output)
    assert first == second
    assert (ledger / "shadow_manifest.json").read_bytes() == first_bytes
    assert first["bets"] == 0 and first["promotion_eligible"] is False
