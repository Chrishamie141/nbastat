"""Paired comparison of two frozen NFL distribution research runs."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence


LAUNCH_MARKETS = ("receptions", "receiving_yards", "rushing_yards")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _key(row: dict[str, Any]) -> tuple[int, int, str, str, str]:
    return (int(row["season"]), int(row["week"]), str(row["game_id"]),
            str(row["canonical_player_id"]), str(row["market"]))


def _metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"rows": 0, "mae": None, "rmse": None, "crps": None}
    absolute = [abs(float(row["actual"]) - float(row["expected_output"])) for row in rows]
    squared = [(float(row["actual"]) - float(row["expected_output"])) ** 2 for row in rows]
    return {"rows": len(rows), "mae": sum(absolute) / len(rows),
            "rmse": math.sqrt(sum(squared) / len(rows)),
            "crps": sum(float(row["crps"]) for row in rows) / len(rows)}


def compare_runs(baseline_path: Path, candidate_path: Path) -> dict[str, Any]:
    baseline_rows = json.loads(baseline_path.read_text(encoding="utf-8"))
    candidate_rows = json.loads(candidate_path.read_text(encoding="utf-8"))
    baseline = {_key(row): row for row in baseline_rows if row.get("market") in LAUNCH_MARKETS}
    candidate = {_key(row): row for row in candidate_rows if row.get("market") in LAUNCH_MARKETS}
    keys = sorted(set(baseline) & set(candidate))
    inconsistent = [key for key in keys if float(baseline[key]["actual"]) != float(candidate[key]["actual"])]
    if inconsistent:
        raise ValueError(f"paired outcomes differ for {len(inconsistent)} keys")
    groups = []
    for market in ("ALL", *LAUNCH_MARKETS):
        selected = [key for key in keys if market == "ALL" or key[-1] == market]
        old = _metrics([baseline[key] for key in selected])
        new = _metrics([candidate[key] for key in selected])
        groups.append({"market": market, "baseline": old, "candidate": new,
                       "delta_candidate_minus_baseline": {
                           name: None if new[name] is None or old[name] is None else new[name] - old[name]
                           for name in ("mae", "rmse", "crps")
                       }})
    overall = groups[0]["delta_candidate_minus_baseline"]
    return {
        "schema_version": 1, "comparison": "SYSTEM_A_LEDGER_FEATURES_VS_FROZEN_DISTRIBUTION_RESEARCH",
        "pairing_key": ["season", "week", "game_id", "canonical_player_id", "market"],
        "matched_rows": len(keys), "markets": groups,
        "assessment": {
            "mae_improved": overall["mae"] < 0, "crps_improved": overall["crps"] < 0,
            "promotion_supported": overall["mae"] < 0 and overall["crps"] < 0,
            "decision": "RETAIN_RESEARCH_ONLY" if overall["mae"] >= 0 or overall["crps"] >= 0 else "ELIGIBLE_FOR_FURTHER_GATES",
        },
        "inputs": {
            "baseline": {"artifact": baseline_path.name, "sha256": _hash(baseline_path)},
            "candidate": {"artifact": candidate_path.name, "sha256": _hash(candidate_path)},
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = compare_runs(args.baseline, args.candidate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
