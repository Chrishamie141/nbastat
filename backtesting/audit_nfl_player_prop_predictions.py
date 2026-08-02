"""Deterministic, offline directionality audit for NFL player-prop forecasts."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .config import SNAPSHOTS_DIR
from .evaluate_nfl_player_props import evaluate, probability_metrics
from .markets import CANONICAL_PLAYER_PROP_MARKETS
from .player_identity import first_player_id, normalize_player_id
from .snapshots import snapshot_week_dir

TOLERANCE = 1e-12

RECOVERABLE_AUDIT_CODES = frozenset({
    "MISSING_OPPOSITE_SIDE", "MISSING_PUSH_PROBABILITY", "MISSING_DISTRIBUTION_SUMMARY",
    "NOT_READY_PREDICTION", "INCOMPLETE_BASE_OPPORTUNITY", "MISSING_OUTCOME_FOR_AUDIT",
    "MISSING_MARKET_PRICE", "COVERAGE_GAP",
})
FATAL_AUDIT_CODES = frozenset({
    "DUPLICATE_CANONICAL_PREDICTION_KEY", "CONFLICTING_DUPLICATE_PREDICTION",
    "PROBABILITY_OUT_OF_RANGE", "PROBABILITY_SUM_INCOHERENT", "SIDE_PROBABILITY_MISMATCH",
    "WRONG_LINE_LOOKUP", "WRONG_PLAYER_LOOKUP", "WRONG_GAME_LOOKUP", "POST_CUTOFF_PREDICTION",
    "OUTCOME_USED_IN_PREDICTION_PROVENANCE", "INVALID_MODEL_PROVENANCE",
})


class PredictionAuditIntegrityError(RuntimeError):
    """Fatal prediction invariants failed after audit artifacts were persisted."""

    def __init__(self, findings: list[dict[str, Any]], artifact_directory: Path | None = None):
        self.findings = findings
        self.fatal_count = sum(not item["recoverable"] for item in findings)
        self.recoverable_count = sum(item["recoverable"] for item in findings)
        self.artifact_directory = artifact_directory
        super().__init__(
            f"prediction audit found {self.fatal_count} fatal and "
            f"{self.recoverable_count} recoverable validation findings"
        )


def _validation_finding(row: dict[str, Any], code: str, *, severity: str,
                        recoverable: bool, observed: Any, expected: str,
                        detail: str) -> dict[str, Any]:
    """Return a stable, JSON-safe finding without embedding raw source records."""
    return {"code": code, "severity": severity, "scope": "PREDICTION",
            "week": row.get("week"), "game_id": row.get("game_id"),
            "canonical_player_id": normalize_player_id(first_player_id(
                row.get("canonical_player_id"), row.get("player_id"))),
            "market": row.get("market"), "line": row.get("line"),
            "side": str(row.get("side") or row.get("selection") or "").upper() or None,
            "observed": observed, "expected_invariant": expected,
            "recoverable": recoverable, "detail": detail[:500]}


def prediction_key(row: dict[str, Any]) -> tuple[Any, ...] | None:
    """The complete side-specific key used by prediction lookup."""
    pid = normalize_player_id(first_player_id(row.get("canonical_player_id"), row.get("player_id")))
    side = str(row.get("side") or row.get("selection") or "").upper()
    try:
        return (str(row["game_id"]), pid, str(row["market"]), float(row["line"]), side) if pid and side in {"OVER", "UNDER"} else None
    except (KeyError, TypeError, ValueError):
        return None


def base_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (int(row["season"]), int(row["week"]), str(row["game_id"]),
            str(row["canonical_player_id"]), str(row["market"]), float(row["line"]))


def _metric_variant(rows: Iterable[dict[str, Any]], transform: str) -> dict[str, Any]:
    changed = []
    for row in rows:
        value = float(row["model_probability"])
        if transform == "swap":
            opposite = row.get("opposite_probability")
            value = float(opposite) if opposite is not None else 1.0 - value - float(row.get("push_probability") or 0)
        elif transform == "complement":
            value = 1.0 - value
        changed.append({**row, "audit_probability": value})
    return probability_metrics(changed, "audit_probability")


def _choose(rows: Iterable[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[base_key(row)].append(row)
    # This selection only reads pregame probabilities.  OVER is the documented tie break.
    return [max(groups[key], key=lambda r: (float(r.get(field) or -1), r["side"] == "OVER"))
            for key in sorted(groups)]


def _distribution_rows(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in predictions:
        grouped[(row.get("season"), row.get("week"), row.get("game_id"),
                 row.get("canonical_player_id"), row.get("market"))].append(row)
    output = []
    for key in sorted(grouped, key=str):
        rows = grouped[key]; ready = next((r for r in rows if r.get("readiness") == "READY"), rows[0])
        summary = ready.get("distribution_summary") or {}
        push_by_line = {str(line): next((float(r.get("push_probability") or 0) for r in rows if float(r["line"]) == line), 0.0)
                        for line in sorted({float(r["line"]) for r in rows})}
        output.append({"season": key[0], "week": key[1], "game_id": key[2],
            "canonical_player_id": key[3], "market": key[4], "simulations": ready.get("simulations"),
            "simulation_seed": ready.get("simulation_seed"), **summary, "push_mass_by_line": push_by_line,
            "source_history_row_count": (ready.get("provenance") or {}).get("player_history_games"),
            "readiness_reason": ready.get("readiness"), "model_version": ready.get("model_version")})
    return output


def audit(snapshot_root: Path, season: int, start_week: int, end_week: int, *,
          market: str | None = None, game_id: str | None = None,
          player_id: str | None = None, top_extremes: int = 100,
          validate: bool = False, output_dir: Path | None = None) -> dict[str, Any]:
    evaluation = evaluate(snapshot_root, season, start_week, end_week, market)
    predictions: list[dict[str, Any]] = []
    for week in range(start_week, end_week + 1):
        path = snapshot_week_dir(snapshot_root, "nfl", season, week) / "player_prop_predictions.json"
        if path.exists(): predictions.extend(json.loads(path.read_text(encoding="utf-8")))
    wanted_pid = normalize_player_id(player_id)
    def wanted(row: dict[str, Any]) -> bool:
        return ((market is None or row.get("market") == market) and
                (game_id is None or str(row.get("game_id")) == game_id) and
                (wanted_pid is None or normalize_player_id(row.get("canonical_player_id")) == wanted_pid))
    predictions = [r for r in predictions if wanted(r)]
    opportunities = [r for r in evaluation["opportunity_rows"] if wanted(r)]
    quote_rows = [r for r in evaluation["quote_rows"] if wanted(r)]

    keyed: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    bases: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in predictions:
        key = prediction_key(row)
        if key is not None: keyed[key].append(row)
        if key is not None: bases[(row.get("season"), row.get("week"), *key[:-1])].append(row)
    duplicate_keys = [{"key": list(k), "count": len(v)} for k, v in sorted(keyed.items(), key=str) if len(v) != 1]
    omitted_field_collisions = {}
    fields = ("game_id", "canonical_player_id", "market", "line", "side")
    for omitted in fields:
        position = fields.index(omitted)
        reduced: dict[tuple[Any, ...], set[tuple[Any, ...]]] = defaultdict(set)
        for key in keyed:
            reduced[key[:position] + key[position + 1:]].add(key)
        omitted_field_collisions[omitted] = [
            {"reduced_key": list(key), "distinct_full_keys": len(full)}
            for key, full in sorted(reduced.items(), key=lambda item: str(item[0])) if len(full) > 1]
    coherence = []
    findings: list[dict[str, Any]] = []
    for key, rows in sorted(keyed.items(), key=lambda item: str(item[0])):
        if len(rows) > 1:
            normalized = {json.dumps(row, sort_keys=True, default=str) for row in rows}
            code = "DUPLICATE_CANONICAL_PREDICTION_KEY" if len(normalized) == 1 else "CONFLICTING_DUPLICATE_PREDICTION"
            findings.append(_validation_finding(rows[0], code, severity="CRITICAL",
                recoverable=False, observed={"row_count": len(rows)},
                expected="exactly one prediction per canonical game/player/market/line/side key",
                detail="Multiple prediction rows resolve to the same canonical key."))
    for row in predictions:
        probability = row.get("model_probability")
        if probability is not None and (not isinstance(probability, (int, float)) or not 0 <= float(probability) <= 1):
            findings.append(_validation_finding(row, "PROBABILITY_OUT_OF_RANGE", severity="CRITICAL",
                recoverable=False, observed={"model_probability": probability},
                expected="model probability is between zero and one inclusive",
                detail="A model probability is outside its valid range."))
        ready = row.get("readiness") == "READY"
        if not ready:
            findings.append(_validation_finding(row, "NOT_READY_PREDICTION", severity="WARNING",
                recoverable=True, observed={"readiness": row.get("readiness")},
                expected="READY prediction for probability audit", detail="Prediction is not ready for grading."))
        # A not-ready row deliberately has no simulated probability payload. Report its
        # readiness reason once instead of misclassifying the consequential null fields
        # as independent missing-output defects.
        if ready and row.get("push_probability") is None:
            findings.append(_validation_finding(row, "MISSING_PUSH_PROBABILITY", severity="WARNING",
                recoverable=True, observed={"push_probability": None},
                expected="push probability is recorded", detail="Push probability is unavailable."))
        if ready and not row.get("distribution_summary"):
            findings.append(_validation_finding(row, "MISSING_DISTRIBUTION_SUMMARY", severity="WARNING",
                recoverable=True, observed={"distribution_summary": "missing"},
                expected="simulation distribution summary is recorded", detail="Distribution diagnostics are unavailable."))
    for key, rows in sorted(bases.items(), key=lambda item: str(item[0])):
        sides = Counter(str(r.get("side")).upper() for r in rows)
        representative = rows[0]; over = representative.get("over_probability"); under = representative.get("under_probability"); push = representative.get("push_probability")
        total = None if None in (over, under, push) else float(over) + float(under) + float(push)
        coherence.append({"base_key": list(key), "over_count": sides["OVER"], "under_count": sides["UNDER"],
                          "probability_sum": total, "coherent": sides["OVER"] == sides["UNDER"] == 1 and total is not None and abs(total - 1) <= TOLERANCE})
        if sides["OVER"] == 0 or sides["UNDER"] == 0:
            findings.append(_validation_finding(representative, "MISSING_OPPOSITE_SIDE", severity="WARNING",
                recoverable=True, observed={"over_count": sides["OVER"], "under_count": sides["UNDER"]},
                expected="one OVER and one UNDER prediction", detail="The opposite-side prediction is unavailable."))
        if total is not None and abs(total - 1) > TOLERANCE:
            findings.append(_validation_finding(representative, "PROBABILITY_SUM_INCOHERENT", severity="CRITICAL",
                recoverable=False, observed={"over_plus_under_plus_push": total},
                expected="OVER + UNDER + PUSH equals one", detail="Side and push probabilities do not sum to one."))
        for row in rows:
            side = str(row.get("side") or "").upper()
            expected_probability = row.get("over_probability" if side == "OVER" else "under_probability")
            if expected_probability is not None and row.get("model_probability") is not None and abs(float(expected_probability)-float(row["model_probability"])) > TOLERANCE:
                findings.append(_validation_finding(row, "SIDE_PROBABILITY_MISMATCH", severity="CRITICAL",
                    recoverable=False, observed={"model_probability": row.get("model_probability"), "side_probability": expected_probability},
                    expected="side row probability equals the corresponding named-side probability",
                    detail="The side-specific probability does not match the named probability."))

    by_base: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in opportunities: by_base[base_key(row)][row["side"]] = row
    audit_rows = []
    prediction_lookup = {prediction_key(r): r for r in predictions if prediction_key(r) is not None}
    for key, sides in sorted(by_base.items()):
        for side, row in sorted(sides.items()):
            opposite = sides.get("UNDER" if side == "OVER" else "OVER")
            actual = float(row["outcome"]); line = float(row["line"])
            winner = "PUSH" if actual == line else "OVER" if actual > line else "UNDER"
            prediction = prediction_lookup.get(prediction_key(row), {})
            audit_rows.append({**row, "actual_stat": actual, "actual_winning_side": winner,
                "opposite_probability": opposite.get("model_probability") if opposite else None,
                "push_probability": prediction.get("push_probability"),
                "probability_assigned_to_actual_winner": row["model_probability"] if side == winner else (opposite or {}).get("model_probability"),
                "probability_assigned_to_loser": row["model_probability"] if side != winner and winner != "PUSH" else (opposite or {}).get("model_probability"),
                "simulation_summary": prediction.get("distribution_summary"), "simulation_seed": prediction.get("simulation_seed"),
                "simulations": prediction.get("simulations"), "readiness": prediction.get("readiness"),
                "model_version": prediction.get("model_version"), "provenance": prediction.get("provenance")})

    current = _metric_variant(audit_rows, "current"); swapped = _metric_variant(audit_rows, "swap"); complement = _metric_variant(audit_rows, "complement")
    dramatic = bool(current["brier_score"] is not None and min(x["brier_score"] for x in (swapped, complement) if x["brier_score"] is not None) < current["brier_score"] * .75)
    side_swap = {"current": current, "over_under_swapped": swapped, "one_minus_current": complement,
                 "likely_directionality_bug": dramatic,
                 "brier_improvement_from_swap": None if current["brier_score"] is None else current["brier_score"] - swapped["brier_score"],
                 "log_loss_improvement_from_swap": None if current["log_loss"] is None else current["log_loss"] - swapped["log_loss"]}
    both = [r for key, sides in by_base.items() if set(sides) == {"OVER", "UNDER"} for r in sides.values()]
    deterministic_one_per_base = [sides[sorted(sides)[0]] for key, sides in sorted(by_base.items()) if sides]
    breakdowns = {"all_side_forecasts": current,
        "over_only": probability_metrics([r for r in audit_rows if r["side"] == "OVER"], "model_probability"),
        "under_only": probability_metrics([r for r in audit_rows if r["side"] == "UNDER"], "model_probability"),
        "one_deterministic_forecast_per_base_opportunity": probability_metrics(deterministic_one_per_base, "model_probability"),
        "base_opportunities_with_both_sides": probability_metrics(both, "model_probability"),
        "model_favored_side": probability_metrics(_choose(audit_rows, "model_probability"), "model_probability"),
        "market_favored_side": probability_metrics(_choose(audit_rows, "no_vig_market_probability"), "model_probability"),
        "best_price_selected_opportunities": probability_metrics(audit_rows, "model_probability"),
        "all_quote_rows": probability_metrics(quote_rows, "model_probability")}
    for name in sorted({str(r["market"]) for r in audit_rows}):
        breakdowns.setdefault("by_market", {})[name] = probability_metrics([r for r in audit_rows if r["market"] == name], "model_probability")

    distributions = _distribution_rows(predictions)
    centers = {}
    for name in sorted({str(r["market"]) for r in predictions}):
        rows = [r for r in predictions if r["market"] == name and r.get("distribution_summary")]
        centers[name] = {"count": len(rows),
            "mean_simulated_stat_minus_line": sum(r["distribution_summary"]["mean"] - float(r["line"]) for r in rows) / len(rows) if rows else None,
            "median_simulated_stat_minus_line": sum(r["distribution_summary"]["median"] - float(r["line"]) for r in rows) / len(rows) if rows else None,
            "lines_below_simulated_minimum_pct": sum(float(r["line"]) < r["distribution_summary"]["minimum"] for r in rows) / len(rows) if rows else None,
            "lines_above_simulated_maximum_pct": sum(float(r["line"]) > r["distribution_summary"]["maximum"] for r in rows) / len(rows) if rows else None,
            "probabilities_under_5_pct": sum(float(r["model_probability"]) < .05 for r in rows) / len(rows) if rows else None,
            "probabilities_over_95_pct": sum(float(r["model_probability"]) > .95 for r in rows) / len(rows) if rows else None}
    extremes = sorted(audit_rows, key=lambda r: (-abs(float(r["model_probability"]) - .5), base_key(r), r["side"]))[:top_extremes]
    summary = {"schema_version": 1, "season": season, "weeks": [start_week, end_week],
        "prediction_rows": len(predictions), "gradeable_side_forecasts": len(audit_rows),
        "base_opportunities": len(by_base), "both_sides_evaluated": len(both) == len(audit_rows),
        "duplicate_prediction_keys": len(duplicate_keys), "incoherent_base_opportunities": sum(not r["coherent"] for r in coherence),
        "likely_directionality_bug": dramatic, "network_contacted": False,
        "filters": {"market": market, "game_id": game_id, "player_id": player_id},
        "market_center_diagnostics": centers,
        "validation": {"enabled": validate, "finding_count": len(findings),
            "fatal_count": sum(not x["recoverable"] for x in findings),
            "recoverable_count": sum(x["recoverable"] for x in findings)}}
    report = {"summary": summary, "rows": audit_rows, "extremes": extremes, "side_swap": side_swap,
            "distributions": distributions, "breakdowns": breakdowns,
            "key_diagnostics": {"duplicates": duplicate_keys, "omitted_field_collisions": omitted_field_collisions,
                                "overwritten_rows": sum(len(rows) - 1 for rows in keyed.values()),
                                "coherence": coherence}, "validation_findings": findings}
    if output_dir is not None:
        write_outputs(report, output_dir)
        if validate and any(not item["recoverable"] for item in findings):
            raise PredictionAuditIntegrityError(findings, output_dir)
    return report


def write_outputs(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {"probability_audit_summary.json": {**report["summary"], "prediction_key_diagnostics": report["key_diagnostics"]},
        "probability_audit_rows.json": report["rows"], "extreme_predictions.json": report["extremes"],
        "side_swap_comparison.json": report["side_swap"], "distribution_diagnostics.json": report["distributions"],
        "market_side_breakdowns.json": report["breakdowns"],
        "audit_validation_findings.json": report.get("validation_findings", [])}
    hashes = {}
    for name, value in artifacts.items():
        path = output_dir / name; path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"schema_version": 1, "artifacts": hashes, "network_contacted": False}
    (output_dir / "audit_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True); parser.add_argument("--start-week", type=int, default=1)
    parser.add_argument("--end-week", type=int, default=1); parser.add_argument("--snapshot-root", type=Path, default=SNAPSHOTS_DIR)
    parser.add_argument("--output-dir", type=Path, required=True); parser.add_argument("--market", choices=CANONICAL_PLAYER_PROP_MARKETS)
    parser.add_argument("--game-id"); parser.add_argument("--player-id"); parser.add_argument("--top-extremes", type=int, default=100)
    parser.add_argument("--validate", action="store_true", help="Check prediction invariants; recoverable findings are reported and fatal contradictions fail after artifacts are written.")
    args = parser.parse_args(argv)
    report = audit(args.snapshot_root, args.season, args.start_week, args.end_week, market=args.market,
                   game_id=args.game_id, player_id=args.player_id, top_extremes=args.top_extremes,
                   validate=args.validate, output_dir=args.output_dir)
    print(json.dumps(report["summary"], indent=2, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
