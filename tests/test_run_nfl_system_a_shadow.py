from __future__ import annotations

import json
from pathlib import Path

from backtesting.forward_shadow_nfl_player_props import _digest
from backtesting.run_nfl_system_a_shadow import grade_week, preflight, prepare_week


def _arguments(tmp_path: Path) -> dict:
    for name in ("system_a",): (tmp_path / name).mkdir()
    config = tmp_path / "config.json"; config.write_text("{}", encoding="utf-8")
    calibration = tmp_path / "calibration.json"; calibration.write_text("[]", encoding="utf-8")
    history = tmp_path / "history.json"; history.write_text("[]", encoding="utf-8")
    policy_value = {"schema_version": 1, "policy_id": "shadow", "minimum_expected_value": .05}
    policy_value["policy_fingerprint"] = _digest(policy_value)
    policy = tmp_path / "policy.json"; policy.write_text(json.dumps(policy_value), encoding="utf-8")
    return {"snapshot_root": tmp_path / "snapshots", "system_a_dir": tmp_path / "system_a",
            "config_path": config, "calibration_rows_path": calibration,
            "price_history_path": history, "policy_path": policy,
            "ledger_dir": tmp_path / "ledger", "season": 2026, "week": 1,
            "generated_at": "2026-09-01T10:01:00Z", "locked_at": "2026-09-01T10:05:00Z"}


def test_preflight_and_prepare_wait_without_calling_generator(tmp_path: Path, monkeypatch) -> None:
    args = _arguments(tmp_path)
    called = False
    def forbidden(**_kwargs):
        nonlocal called; called = True
        raise AssertionError("generator must not run")
    monkeypatch.setattr("backtesting.run_nfl_system_a_shadow.generate_candidates", forbidden)
    report = prepare_week(**args)
    assert report["status"] == "WAITING_FOR_PREGAME_DATA"
    assert report["paid_credits_used"] == 0 and called is False
    assert (args["ledger_dir"] / "status" / "2026_week_01.json").exists()


def test_prepare_generates_locks_and_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    args = _arguments(tmp_path)
    week = args["snapshot_root"] / "nfl" / "2026" / "week_01"; week.mkdir(parents=True)
    (week / "games.json").write_text("[]", encoding="utf-8")
    (week / "player_prop_odds.json").write_text("[]", encoding="utf-8")
    common = {"season": 2026, "week": 1, "game_id": "g1", "canonical_player_id": "p1",
              "market": "receptions", "line": 4.5, "bookmaker": "book", "decimal_odds": 2.0,
              "quote_timestamp": "2026-09-01T10:00:00Z", "generated_at": "2026-09-01T10:01:00Z",
              "prediction_cutoff": "2026-09-01T11:00:00Z"}
    candidates = [{**common, "side": "OVER", "policy_probability": .6, "push_probability": 0},
                  {**common, "side": "UNDER", "policy_probability": .4, "push_probability": 0}]
    def fake_generate(*, output_path, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(candidates), encoding="utf-8")
        return {"candidates": candidates, "manifest": {"candidate_rows": 2}}
    monkeypatch.setattr("backtesting.run_nfl_system_a_shadow.generate_candidates", fake_generate)
    first = prepare_week(**args); second = prepare_week(**args)
    assert first["status"] == "LOCKED" and first["candidate_rows"] == 2
    assert first["ledger_entry"]["batch_fingerprint"] == second["ledger_entry"]["batch_fingerprint"]
    assert len(list((args["ledger_dir"] / "entries").glob("*.json"))) == 1


def test_grade_waits_then_grades_all_matching_entries(tmp_path: Path, monkeypatch) -> None:
    args = _arguments(tmp_path)
    waiting = grade_week(snapshot_root=args["snapshot_root"], ledger_dir=args["ledger_dir"], season=2026, week=1)
    assert waiting["status"] == "WAITING_FOR_COMPLETED_PLAYER_STATS"
    # Reuse the locking fixture path with a direct minimal ledger entry.
    week = args["snapshot_root"] / "nfl" / "2026" / "week_01"; week.mkdir(parents=True, exist_ok=True)
    (week / "games.json").write_text("[]", encoding="utf-8")
    (week / "player_prop_odds.json").write_text("[]", encoding="utf-8")
    common = {"season": 2026, "week": 1, "game_id": "g1", "canonical_player_id": "p1",
              "market": "receptions", "line": 4.5, "bookmaker": "book", "decimal_odds": 2.0,
              "quote_timestamp": "2026-09-01T10:00:00Z", "generated_at": "2026-09-01T10:01:00Z",
              "prediction_cutoff": "2026-09-01T11:00:00Z"}
    candidates = [{**common, "side": "OVER", "policy_probability": .6},
                  {**common, "side": "UNDER", "policy_probability": .4}]
    def fake_generate(*, output_path, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True); output_path.write_text(json.dumps(candidates), encoding="utf-8")
        return {"candidates": candidates, "manifest": {}}
    monkeypatch.setattr("backtesting.run_nfl_system_a_shadow.generate_candidates", fake_generate)
    prepare_week(**args)
    (week / "player_stats.json").write_text(json.dumps([{"game_id": "g1", "canonical_player_id": "p1",
        "record_role": "game_outcome", "is_pregame": False, "completed_at": "2026-09-01T15:00:00Z",
        "stats": {"receptions": 6}}]), encoding="utf-8")
    result = grade_week(snapshot_root=args["snapshot_root"], ledger_dir=args["ledger_dir"], season=2026, week=1)
    assert result["status"] == "GRADED" and result["graded_batches"] == 1
    assert result["shadow_summary"]["wins"] == 1


def test_preflight_reports_hashes_without_network(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"; week = root / "nfl" / "2026" / "week_01"; week.mkdir(parents=True)
    (week / "games.json").write_text("[]", encoding="utf-8")
    report = preflight(root, 2026, 1)
    assert report["games"]["sha256"] and report["quotes"]["sha256"] is None
    assert report["network_contacted"] is False and report["paid_credits_used"] == 0
