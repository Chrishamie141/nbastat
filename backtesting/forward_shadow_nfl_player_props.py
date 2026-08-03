"""Freeze and operate an append-only NFL player-prop forward-shadow ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .game_matching import parse_dt


SCHEMA_VERSION = 1
POLICY_ID = "nfl_system_a_forward_shadow_v1"
REQUIRED_CANDIDATE_FIELDS = {
    "season", "week", "game_id", "canonical_player_id", "market", "line", "side",
    "policy_probability", "decimal_odds", "bookmaker", "quote_timestamp", "generated_at",
    "prediction_cutoff",
}


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def freeze_policy(report_path: Path, calibration_manifest: Path, output_path: Path) -> dict[str, Any]:
    """Freeze the final configuration that was selected before its test outcome."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    eligible = [fold for fold in report["folds"] if fold.get("selected_configuration") and
                fold.get("selected_threshold") is not None]
    if not eligible:
        raise ValueError("historical nested report has no pre-outcome selected policy")
    source = max(eligible, key=lambda fold: (int(fold["test_season"]), int(fold["test_week"])))
    policy = {
        "schema_version": SCHEMA_VERSION, "policy_id": POLICY_ID, "state": "FROZEN_SHADOW_ONLY",
        "configuration": source["selected_configuration"],
        "minimum_expected_value": float(source["selected_threshold"]),
        "selection_contract": "AT_MOST_ONE_SIDE_PER_PLAYER_GAME_MARKET_LINE_ELSE_PASS",
        "stake_units": 1.0, "production_wagering_authorized": False,
        "source_fold": {
            "test_season": int(source["test_season"]), "test_week": int(source["test_week"]),
            "inner_periods": source["inner_periods"],
            "selection_timing": "SELECTED_FROM_INNER_PERIODS_BEFORE_SOURCE_TEST_OUTCOMES",
        },
        "probability_contract": "PRICE_BLIND_SYSTEM_A_CALIBRATION_THEN_FROZEN_MARKET_RESIDUAL",
        "price_contract": "BEST_FROZEN_PREGAME_EXECUTION_PRICE_AND_COMPLETE_NO_VIG_PAIR",
        "inputs": {
            report_path.as_posix(): _file_hash(report_path),
            calibration_manifest.as_posix(): _file_hash(calibration_manifest),
        },
        "guardrails": [
            "No historical result authorizes real wagering.",
            "Every shadow decision must be locked no later than its prediction cutoff.",
            "Policy changes require a new policy id and cannot rewrite existing ledger entries.",
        ],
    }
    policy["policy_fingerprint"] = _digest(policy)
    if output_path.exists():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        if existing != policy:
            raise ValueError("frozen policy already exists with different content")
        return existing
    _write_json(output_path, policy)
    return policy


def _base_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (int(row["season"]), int(row["week"]), str(row["game_id"]),
            str(row["canonical_player_id"]), str(row["market"]), float(row["line"]))


def _candidate_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (*_base_key(row), str(row["side"]))


def _safe_candidate(raw: dict[str, Any], locked_at: datetime) -> dict[str, Any]:
    missing = sorted(REQUIRED_CANDIDATE_FIELDS - set(raw))
    if missing:
        raise ValueError(f"shadow candidate missing fields: {missing}")
    if any(field in raw for field in ("actual", "outcome", "grade", "result", "profit_units")):
        raise ValueError("shadow candidate contains outcome-bearing fields")
    side = str(raw["side"]).upper()
    if side not in {"OVER", "UNDER"}:
        raise ValueError(f"invalid side: {side}")
    cutoff = parse_dt(raw["prediction_cutoff"])
    generated = parse_dt(raw["generated_at"])
    quoted = parse_dt(raw["quote_timestamp"])
    if not cutoff or not generated or not quoted:
        raise ValueError("candidate timestamps must be valid")
    if generated > cutoff or quoted > cutoff or locked_at > cutoff:
        raise ValueError("candidate, quote, or lock timestamp is after prediction cutoff")
    if generated > locked_at or quoted > locked_at:
        raise ValueError("candidate or quote timestamp is after ledger lock time")
    probability, decimal = float(raw["policy_probability"]), float(raw["decimal_odds"])
    push = float(raw.get("push_probability") or 0.0)
    if not 0 <= probability <= 1 or not 0 <= push <= 1 or probability + push > 1 + 1e-9:
        raise ValueError("invalid probability values")
    if decimal <= 1:
        raise ValueError("decimal odds must exceed one")
    return {
        "season": int(raw["season"]), "week": int(raw["week"]),
        "game_id": str(raw["game_id"]), "canonical_player_id": str(raw["canonical_player_id"]),
        "player_name": raw.get("player_name"), "team": raw.get("team"),
        "market": str(raw["market"]), "line": float(raw["line"]), "side": side,
        "policy_probability": probability, "push_probability": push,
        "decimal_odds": decimal, "american_odds": raw.get("american_odds"),
        "bookmaker": str(raw["bookmaker"]), "quote_timestamp": quoted.isoformat().replace("+00:00", "Z"),
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "prediction_cutoff": cutoff.isoformat().replace("+00:00", "Z"),
        "probability_provenance": raw.get("probability_provenance"),
        "price_provenance": raw.get("price_provenance"),
    }


def _expected_value(row: dict[str, Any]) -> float:
    win = float(row["policy_probability"])
    push = float(row["push_probability"])
    loss = max(0.0, 1.0 - win - push)
    return win * (float(row["decimal_odds"]) - 1.0) - loss


def build_decisions(candidates: Sequence[dict[str, Any]], policy: dict[str, Any], *, locked_at: str) -> list[dict[str, Any]]:
    lock = parse_dt(locked_at)
    if lock is None:
        raise ValueError("locked_at must be a valid timestamp")
    safe = [_safe_candidate(row, lock) for row in candidates]
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in safe:
        grouped[_base_key(row)].append(row)
    duplicate_keys = len(safe) - len({_candidate_key(row) for row in safe})
    if duplicate_keys:
        raise ValueError(f"duplicate shadow candidate keys: {duplicate_keys}")
    decisions = []
    threshold = float(policy["minimum_expected_value"])
    for key in sorted(grouped):
        rows = grouped[key]
        if {row["side"] for row in rows} != {"OVER", "UNDER"} or len(rows) != 2:
            raise ValueError(f"incomplete side pair for {key}")
        ranked = sorted(((_expected_value(row), row["side"], row) for row in rows),
                        key=lambda value: (value[0], value[1]), reverse=True)
        best_ev, _side, best = ranked[0]
        selected = best if best_ev >= threshold else None
        decision = {
            "base_key": list(key), "decision": "BET" if selected else "PASS",
            "selected_side": selected["side"] if selected else None,
            "expected_value": best_ev if selected else None,
            "minimum_expected_value": threshold, "selected_candidate": selected,
            "candidate_sides": [{**row, "expected_value": _expected_value(row)} for row in rows],
            "locked_at": lock.isoformat().replace("+00:00", "Z"),
            "policy_id": policy["policy_id"], "policy_fingerprint": policy["policy_fingerprint"],
            "shadow_only": True,
        }
        decision["decision_fingerprint"] = _digest(decision)
        decisions.append(decision)
    return decisions


def lock_batch(candidates_path: Path, policy_path: Path, ledger_dir: Path, *, locked_at: str) -> dict[str, Any]:
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if policy.get("policy_fingerprint") != _digest({k: v for k, v in policy.items() if k != "policy_fingerprint"}):
        raise ValueError("frozen policy fingerprint is invalid")
    decisions = build_decisions(candidates, policy, locked_at=locked_at)
    normalized_lock = decisions[0]["locked_at"] if decisions else parse_dt(locked_at).isoformat().replace("+00:00", "Z")
    batch = {
        "schema_version": SCHEMA_VERSION, "policy_id": policy["policy_id"],
        "policy_fingerprint": policy["policy_fingerprint"], "locked_at": normalized_lock,
        "candidate_input": {"path": candidates_path.as_posix(), "sha256": _file_hash(candidates_path)},
        "decisions": decisions,
    }
    batch["batch_fingerprint"] = _digest(batch)
    entries = ledger_dir / "entries"; entries.mkdir(parents=True, exist_ok=True)
    for existing_path in sorted(entries.glob("*.json")):
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
        old = {row["decision_fingerprint"]: row for row in existing.get("decisions", [])}
        if decisions and all(row["decision_fingerprint"] in old for row in decisions):
            return {"entry_path": existing_path.as_posix(), "batch": existing}
        for row in decisions:
            if row["decision_fingerprint"] in old:
                continue
            if any(item.get("base_key") == row["base_key"] for item in existing.get("decisions", [])):
                raise ValueError(f"append-only ledger already has a different decision for {row['base_key']}")
    path = entries / f"{batch['batch_fingerprint']}.json"
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != batch:
            raise ValueError("existing shadow batch content differs")
    else:
        _write_json(path, batch)
    return {"entry_path": path.as_posix(), "batch": batch}


def _stat(outcome: dict[str, Any], market: str) -> float | None:
    value = outcome.get(market)
    if value is None and isinstance(outcome.get("stats"), dict):
        value = outcome["stats"].get(market)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def grade_batch(entry_path: Path, outcomes_path: Path, grades_dir: Path) -> dict[str, Any]:
    batch = json.loads(entry_path.read_text(encoding="utf-8"))
    outcomes = json.loads(outcomes_path.read_text(encoding="utf-8"))
    index = {}
    for row in outcomes:
        if row.get("is_pregame") is not False and row.get("record_role") != "game_outcome":
            continue
        index[(str(row.get("game_id")), str(row.get("canonical_player_id")))] = row
    grades = []
    for decision in batch["decisions"]:
        selected = decision.get("selected_candidate")
        if not selected:
            grades.append({"decision_fingerprint": decision["decision_fingerprint"], "grade": "PASS",
                           "profit_units": 0.0})
            continue
        outcome = index.get((selected["game_id"], selected["canonical_player_id"]))
        actual = None if outcome is None else _stat(outcome, selected["market"])
        completed = None if outcome is None else parse_dt(outcome.get("completed_at") or outcome.get("data_as_of"))
        cutoff = parse_dt(selected["prediction_cutoff"])
        if actual is not None and completed is not None and cutoff is not None and completed <= cutoff:
            raise ValueError("outcome completion timestamp is not after prediction cutoff")
        if actual is None:
            grades.append({"decision_fingerprint": decision["decision_fingerprint"], "grade": "PENDING",
                           "profit_units": None})
            continue
        line, side = float(selected["line"]), selected["side"]
        grade = "PUSH" if actual == line else "WIN" if (
            side == "OVER" and actual > line or side == "UNDER" and actual < line
        ) else "LOSS"
        profit = 0.0 if grade == "PUSH" else float(selected["decimal_odds"]) - 1 if grade == "WIN" else -1.0
        grades.append({"decision_fingerprint": decision["decision_fingerprint"], "grade": grade,
                       "actual": actual, "profit_units": profit})
    report = {
        "schema_version": SCHEMA_VERSION, "batch_fingerprint": batch["batch_fingerprint"],
        "entry_sha256": _file_hash(entry_path),
        "outcomes": {"path": outcomes_path.as_posix(), "sha256": _file_hash(outcomes_path)},
        "grades": grades,
    }
    report["grade_fingerprint"] = _digest(report)
    grades_dir.mkdir(parents=True, exist_ok=True)
    path = grades_dir / f"{batch['batch_fingerprint']}.{_file_hash(outcomes_path)[:16]}.json"
    if path.exists() and json.loads(path.read_text(encoding="utf-8")) != report:
        raise ValueError("grade artifact already exists with different outcomes")
    if not path.exists():
        _write_json(path, report)
    return report


def summarize(ledger_dir: Path) -> dict[str, Any]:
    entries = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((ledger_dir / "entries").glob("*.json"))]
    grades = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((ledger_dir / "grades").glob("*.json"))]
    grade_map: dict[str, dict[str, Any]] = {}
    for report in grades:
        for row in report["grades"]:
            key = row["decision_fingerprint"]
            current = grade_map.get(key)
            if current and current.get("grade") in {"WIN", "LOSS", "PUSH"} and row.get("grade") in {"WIN", "LOSS", "PUSH"} and current != row:
                raise ValueError(f"conflicting settled shadow grades for {key}")
            if current is None or current.get("grade") == "PENDING" or row.get("grade") in {"WIN", "LOSS", "PUSH"}:
                grade_map[key] = row
    decisions = [row for batch in entries for row in batch["decisions"]]
    bets = [row for row in decisions if row["decision"] == "BET"]
    settled = [grade_map[row["decision_fingerprint"]] for row in bets
               if grade_map.get(row["decision_fingerprint"], {}).get("grade") in {"WIN", "LOSS", "PUSH"}]
    profit = sum(float(row["profit_units"]) for row in settled)
    risked = sum(row["grade"] in {"WIN", "LOSS"} for row in settled)
    frozen = ledger_dir / "frozen_policy.json"
    frozen_id = json.loads(frozen.read_text(encoding="utf-8")).get("policy_id") if frozen.exists() else None
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_ids": sorted({row["policy_id"] for row in decisions} | ({frozen_id} if frozen_id else set())),
        "batches": len(entries), "base_decisions": len(decisions), "bets": len(bets),
        "passes": len(decisions) - len(bets), "pending_bets": len(bets) - len(settled),
        "settled_bets": len(settled), "wins": sum(row["grade"] == "WIN" for row in settled),
        "losses": sum(row["grade"] == "LOSS" for row in settled),
        "pushes": sum(row["grade"] == "PUSH" for row in settled),
        "profit_units": profit, "roi": profit / risked if risked else None,
        "promotion_eligible": False,
        "promotion_blocker": "FORWARD_SHADOW_SAMPLE_AND_POSITIVE_ROI_LOWER_BOUND_REQUIRED",
    }


def write_summary(ledger_dir: Path, output_path: Path) -> dict[str, Any]:
    value = summarize(ledger_dir)
    _write_json(output_path, value)
    frozen = ledger_dir / "frozen_policy.json"
    prediction = ledger_dir / "frozen_prediction_config.json"
    readiness = ledger_dir / "2026_readiness.json"
    entry_paths = sorted((ledger_dir / "entries").glob("*.json"))
    grade_paths = sorted((ledger_dir / "grades").glob("*.json"))
    manifest = {
        "schema_version": SCHEMA_VERSION, "network_contacted": False,
        "frozen_policy": None if not frozen.exists() else {"path": frozen.as_posix(), "sha256": _file_hash(frozen)},
        "frozen_prediction_config": None if not prediction.exists() else {
            "path": prediction.as_posix(), "sha256": _file_hash(prediction)},
        "readiness": None if not readiness.exists() else {
            "path": readiness.as_posix(), "sha256": _file_hash(readiness)},
        "entries": {path.name: _file_hash(path) for path in entry_paths},
        "grades": {path.name: _file_hash(path) for path in grade_paths},
        "summary": {"path": output_path.as_posix(), "sha256": _file_hash(output_path)},
        "append_only": True, "production_wagering_authorized": False,
    }
    _write_json(ledger_dir / "shadow_manifest.json", manifest)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze-policy")
    freeze.add_argument("--report", type=Path, required=True); freeze.add_argument("--calibration-manifest", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    lock = sub.add_parser("lock")
    lock.add_argument("--candidates", type=Path, required=True); lock.add_argument("--policy", type=Path, required=True)
    lock.add_argument("--ledger-dir", type=Path, required=True); lock.add_argument("--locked-at", required=True)
    grade = sub.add_parser("grade")
    grade.add_argument("--entry", type=Path, required=True); grade.add_argument("--outcomes", type=Path, required=True)
    grade.add_argument("--grades-dir", type=Path, required=True)
    summary = sub.add_parser("summarize"); summary.add_argument("--ledger-dir", type=Path, required=True)
    summary.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "freeze-policy": freeze_policy(args.report, args.calibration_manifest, args.output)
    elif args.command == "lock": lock_batch(args.candidates, args.policy, args.ledger_dir, locked_at=args.locked_at)
    elif args.command == "grade": grade_batch(args.entry, args.outcomes, args.grades_dir)
    else: write_summary(args.ledger_dir, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
