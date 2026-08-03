"""Join System A calibrated probabilities to frozen prices and evaluate EV policy.

Probability fitting remains price-blind.  Sportsbook prices enter only after
both sides are calibrated, when the nested policy selects OVER, UNDER, or PASS.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

from .evaluate_nfl_player_prop_nested_policy import evaluate_nested_policy, write_outputs


MODEL_ID = "nfl_system_a_calibrated_price_policy_research_v1"
LAUNCH_MARKETS = {"receptions", "receiving_yards", "rushing_yards"}


def _key(row: dict[str, Any]) -> tuple[int, int, str, str, str, float, str]:
    return (int(row["season"]), int(row["week"]), str(row["game_id"]),
            str(row["canonical_player_id"]), str(row["market"]), float(row["line"]),
            str(row["side"]))


def _base_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return _key(row)[:-1]


def _load_prices(paths: Sequence[Path]) -> tuple[dict[tuple[Any, ...], dict[str, Any]], dict[str, Any]]:
    prices: dict[tuple[Any, ...], dict[str, Any]] = {}
    duplicates = 0
    for path in paths:
        with path.open(encoding="utf-8", newline="") as handle:
            for raw in csv.DictReader(handle):
                if raw.get("market") not in LAUNCH_MARKETS or raw.get("grade") not in {"WIN", "LOSS", "PUSH"}:
                    continue
                row = {
                    "season": int(raw["season"]), "week": int(raw["week"]),
                    "game_id": raw["game_id"], "canonical_player_id": raw["canonical_player_id"],
                    "market": raw["market"], "line": float(raw["line"]), "side": raw["side"],
                    "bookmaker": raw["bookmaker"], "american_odds": float(raw["american_odds"]),
                    "decimal_odds": float(raw["decimal_odds"]),
                    "no_vig_market_probability": float(raw["no_vig_market_probability"]),
                    "v3_probability": float(raw["model_probability"]), "grade": raw["grade"],
                    "profit_units": float(raw["profit_units"]), "outcome": float(raw["outcome"]),
                    "quote_timestamp": raw.get("quote_timestamp"),
                }
                key = _key(row)
                if key in prices:
                    duplicates += 1
                    # History artifacts promise one deterministic best price.
                    if row != prices[key]:
                        raise ValueError(f"conflicting frozen prices for {key}")
                prices[key] = row
    return prices, {"price_rows": len(prices), "duplicate_identical_rows": duplicates}


def join_calibrated_prices(calibration_rows: Sequence[dict[str, Any]], price_paths: Sequence[Path]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    prices, audit = _load_prices(price_paths)
    ready = [row for row in calibration_rows if row.get("calibration_ready") and
             row.get("market") in LAUNCH_MARKETS and row.get("result") in {"WIN", "LOSS"}]
    by_base: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in ready:
        by_base[_base_key(row)].append(dict(row))
    coherent: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    incoherent_probability_bases = 0
    for key, sides in by_base.items():
        if {row["side"] for row in sides} != {"OVER", "UNDER"}:
            continue
        total = sum(float(row["calibrated_probability"]) for row in sides)
        if total <= 0:
            incoherent_probability_bases += 1
            continue
        coherent[key] = [{**row, "normalized_calibrated_probability":
                          float(row["calibrated_probability"]) / total} for row in sides]

    joined: list[dict[str, Any]] = []
    missing_price = Counter()
    outcome_conflicts = []
    for key in sorted(coherent):
        pair = []
        for calibrated in coherent[key]:
            price = prices.get(_key(calibrated))
            if price is None:
                missing_price[calibrated["side"]] += 1
                continue
            if calibrated["result"] != price["grade"]:
                outcome_conflicts.append(_key(calibrated))
                continue
            pair.append({
                **price, "player_name": calibrated.get("player_name"),
                "team": calibrated.get("team"), "opponent": calibrated.get("opponent"),
                "model_probability": calibrated["normalized_calibrated_probability"],
                "calibrated_probability_before_pair_normalization": calibrated["calibrated_probability"],
                "raw_distribution_probability": calibrated["raw_probability"],
                "baseline_probability": price["v3_probability"], "push_probability": 0.0,
                "calibration_method": calibrated["calibration_method"],
                "calibration_training_rows": calibrated["calibration_training_rows"],
                "research_only": True,
            })
        if len(pair) == 2 and {row["side"] for row in pair} == {"OVER", "UNDER"}:
            joined.extend(pair)
    if outcome_conflicts:
        raise ValueError(f"calibration/price outcome contradictions: {len(outcome_conflicts)}")
    joined.sort(key=_key)
    audit.update({
        "calibration_ready_side_rows": len(ready), "calibration_ready_bases": len(by_base),
        "coherent_calibration_bases": len(coherent),
        "incoherent_probability_bases": incoherent_probability_bases,
        "joined_side_rows": len(joined), "joined_complete_bases": len(joined) // 2,
        "missing_price_by_side": dict(sorted(missing_price.items())),
        "periods": [list(value) for value in sorted({(row["season"], row["week"]) for row in joined})],
        "markets": dict(sorted(Counter(row["market"] for row in joined).items())),
        "books": dict(sorted(Counter(row["bookmaker"] for row in joined).items())),
    })
    return joined, audit


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def run_price_policy(*, calibration_path: Path, price_paths: Sequence[Path], output_dir: Path,
                     bootstrap_draws: int = 2000, seed: int = 1729) -> dict[str, Any]:
    calibration_rows = json.loads(calibration_path.read_text(encoding="utf-8"))
    joined, audit = join_calibrated_prices(calibration_rows, price_paths)
    if not joined:
        raise ValueError("no complete calibrated price pairs were joined")
    report, bets = evaluate_nested_policy(joined, draws=bootstrap_draws, seed=seed)
    report["model_id"] = MODEL_ID
    report["price_join_audit"] = audit
    report["probability_source"] = "SYSTEM_A_PRIOR_FOLD_CALIBRATED_DISTRIBUTION"
    report["price_role"] = "POST_PROBABILITY_ONE_SIDE_OR_PASS_DECISION_ONLY"
    output_dir.mkdir(parents=True, exist_ok=True)
    prepared = output_dir / "price_policy_input_rows.json"
    audit_path = output_dir / "price_join_audit.json"
    _write_json(prepared, joined); _write_json(audit_path, audit)
    write_outputs(report, bets, output_dir, [calibration_path, *price_paths])
    artifact_names = ["price_policy_input_rows.json", "price_join_audit.json",
                      "nested_policy_evaluation.json", "nested_policy_folds.json",
                      "nested_policy_bets.csv", "nested_policy_manifest.json"]
    manifest = {
        "schema_version": 1, "model_id": MODEL_ID, "network_contacted": False,
        "inputs": {path.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in sorted([calibration_path, *price_paths], key=lambda item: item.as_posix())},
        "artifacts": {name: hashlib.sha256((output_dir / name).read_bytes()).hexdigest()
                      for name in artifact_names},
        "seed": seed, "bootstrap_draws": bootstrap_draws,
    }
    _write_json(output_dir / "price_policy_manifest.json", manifest)
    return {"evaluation": report, "bets": bets, "audit": audit, "manifest": manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", dest="calibration_path", type=Path, required=True)
    parser.add_argument("--prices", dest="price_paths", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-draws", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args(argv)
    run_price_policy(**vars(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
