"""Operate the frozen System A NFL shadow workflow without network access."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .forward_shadow_nfl_player_props import grade_batch, lock_batch, write_summary
from .generate_nfl_system_a_shadow_candidates import generate_candidates


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _week_dir(snapshot_root: Path, season: int, week: int) -> Path:
    return snapshot_root / "nfl" / str(season) / f"week_{week:02d}"


def preflight(snapshot_root: Path, season: int, week: int) -> dict[str, Any]:
    directory = _week_dir(snapshot_root, season, week)
    games, quotes = directory / "games.json", directory / "player_prop_odds.json"
    missing = [path.name for path in (games, quotes) if not path.exists()]
    return {
        "schema_version": 1, "season": season, "week": week,
        "snapshot_directory": directory.as_posix(),
        "games": {"present": games.exists(), "sha256": _hash(games) if games.exists() else None},
        "quotes": {"present": quotes.exists(), "sha256": _hash(quotes) if quotes.exists() else None},
        "status": "READY" if not missing else "WAITING_FOR_PREGAME_DATA",
        "missing": missing, "network_contacted": False,
        "paid_credits_used": 0, "production_wagering_authorized": False,
    }


def prepare_week(*, snapshot_root: Path, system_a_dir: Path, config_path: Path,
                 calibration_rows_path: Path, price_history_path: Path, policy_path: Path,
                 ledger_dir: Path, season: int, week: int, generated_at: str,
                 locked_at: str) -> dict[str, Any]:
    check = preflight(snapshot_root, season, week)
    status_dir = ledger_dir / "status"; status_path = status_dir / f"{season}_week_{week:02d}.json"
    if check["status"] != "READY":
        report = {**check, "action": "WAIT", "candidate_rows": 0, "ledger_entry": None}
        _write_json(status_path, report)
        return report
    stamp = generated_at.replace(":", "").replace("-", "").replace("+", "").replace("Z", "Z")
    candidate_path = ledger_dir / "candidates" / f"{season}_week_{week:02d}_{stamp}.json"
    directory = _week_dir(snapshot_root, season, week)
    generated = generate_candidates(
        snapshot_root=snapshot_root, system_a_dir=system_a_dir, config_path=config_path,
        calibration_rows_path=calibration_rows_path, price_history_path=price_history_path,
        games_path=directory / "games.json", quotes_path=directory / "player_prop_odds.json",
        output_path=candidate_path, generated_at=generated_at,
    )
    if not generated["candidates"]:
        report = {**check, "status": "NO_READY_CANDIDATES", "action": "WAIT",
                  "candidate_rows": 0, "candidate_manifest": generated["manifest"], "ledger_entry": None}
        _write_json(status_path, report)
        return report
    locked = lock_batch(candidate_path, policy_path, ledger_dir, locked_at=locked_at)
    summary = write_summary(ledger_dir, ledger_dir / "shadow_summary.json")
    report = {**check, "status": "LOCKED", "action": "SHADOW_ONLY",
              "candidate_rows": len(generated["candidates"]),
              "candidate_artifact": {"path": candidate_path.as_posix(), "sha256": _hash(candidate_path)},
              "ledger_entry": {"path": locked["entry_path"],
                               "batch_fingerprint": locked["batch"]["batch_fingerprint"]},
              "shadow_summary": summary}
    _write_json(status_path, report)
    return report


def grade_week(*, snapshot_root: Path, ledger_dir: Path, season: int, week: int) -> dict[str, Any]:
    outcomes_path = _week_dir(snapshot_root, season, week) / "player_stats.json"
    if not outcomes_path.exists():
        report = {"schema_version": 1, "season": season, "week": week,
                  "status": "WAITING_FOR_COMPLETED_PLAYER_STATS", "graded_batches": 0,
                  "network_contacted": False, "production_wagering_authorized": False}
        _write_json(ledger_dir / "status" / f"{season}_week_{week:02d}_grading.json", report)
        return report
    graded = []
    for entry_path in sorted((ledger_dir / "entries").glob("*.json")):
        batch = json.loads(entry_path.read_text(encoding="utf-8"))
        if not any(tuple(decision["base_key"][:2]) == (season, week) for decision in batch["decisions"]):
            continue
        result = grade_batch(entry_path, outcomes_path, ledger_dir / "grades")
        graded.append({"entry": entry_path.name, "grade_fingerprint": result["grade_fingerprint"]})
    summary = write_summary(ledger_dir, ledger_dir / "shadow_summary.json")
    report = {"schema_version": 1, "season": season, "week": week,
              "status": "GRADED" if graded else "NO_LOCKED_BATCHES", "graded_batches": len(graded),
              "grades": graded, "shadow_summary": summary,
              "network_contacted": False, "production_wagering_authorized": False}
    _write_json(ledger_dir / "status" / f"{season}_week_{week:02d}_grading.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight"); pre.add_argument("--snapshot-root", type=Path, required=True)
    pre.add_argument("--season", type=int, required=True); pre.add_argument("--week", type=int, required=True)
    pre.add_argument("--output", type=Path, required=True)
    prepare = sub.add_parser("prepare")
    for target in (prepare,):
        target.add_argument("--snapshot-root", type=Path, required=True); target.add_argument("--system-a-dir", type=Path, required=True)
        target.add_argument("--config", dest="config_path", type=Path, required=True)
        target.add_argument("--calibration-rows", dest="calibration_rows_path", type=Path, required=True)
        target.add_argument("--price-history", dest="price_history_path", type=Path, required=True)
        target.add_argument("--policy", dest="policy_path", type=Path, required=True)
        target.add_argument("--ledger-dir", type=Path, required=True); target.add_argument("--season", type=int, required=True)
        target.add_argument("--week", type=int, required=True); target.add_argument("--generated-at", required=True)
        target.add_argument("--locked-at", required=True)
    grade = sub.add_parser("grade"); grade.add_argument("--snapshot-root", type=Path, required=True)
    grade.add_argument("--ledger-dir", type=Path, required=True); grade.add_argument("--season", type=int, required=True)
    grade.add_argument("--week", type=int, required=True)
    args = parser.parse_args(argv)
    if args.command == "preflight":
        value = preflight(args.snapshot_root, args.season, args.week); _write_json(args.output, value)
    elif args.command == "prepare":
        prepare_week(**{key: value for key, value in vars(args).items() if key != "command"})
    else:
        grade_week(snapshot_root=args.snapshot_root, ledger_dir=args.ledger_dir, season=args.season, week=args.week)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
