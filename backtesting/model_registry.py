"""Immutable, content-addressed model and experiment registry.

Registry records are append-only JSON files.  Re-registering identical content
is idempotent; attempting to reuse an identifier for different content fails.
Promotion is a separate evidence record and never mutates a model definition.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
DEFAULT_ROOT = Path("backtesting/model_registry")
MODEL_STATES = {"experimental", "candidate", "benchmark", "retired"}
MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,79}$")
PROMOTION_POLICY = {
    "policy_id": "nfl_player_prop_promotion_v1",
    "minimum_evaluated_weeks": 15,
    "minimum_opportunities": 1000,
    "minimum_independent_games": 100,
    "require_leakage_safe": True,
    "require_out_of_sample": True,
    "require_paired_baseline": True,
    "required_improvements": {
        "brier_score_delta": "upper_95_ci_below_zero",
        "log_loss_delta": "upper_95_ci_below_zero",
        "roi_delta": "lower_95_ci_above_zero",
        "ece_delta": "estimate_not_positive",
    },
}


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_bytes(value)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _require(record: dict[str, Any], fields: tuple[str, ...], kind: str) -> None:
    missing = [field for field in fields if record.get(field) in (None, "", [], {})]
    if missing:
        raise ValueError(f"{kind} missing required fields: {', '.join(missing)}")


def validate_model(record: dict[str, Any]) -> None:
    _require(record, ("schema_version", "model_id", "sport", "target", "state", "git_commit",
                      "feature_set", "distribution", "variance", "calibration"), "model")
    if record["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported model schema_version")
    if not MODEL_ID.fullmatch(str(record["model_id"])):
        raise ValueError("invalid model_id")
    if record["state"] not in MODEL_STATES:
        raise ValueError(f"invalid model state: {record['state']}")
    feature_set = record["feature_set"]
    if not isinstance(feature_set, dict) or not isinstance(feature_set.get("features"), list):
        raise ValueError("feature_set.features must be a list")


def validate_experiment(record: dict[str, Any]) -> None:
    _require(record, ("schema_version", "experiment_id", "model_id", "git_commit", "configuration_hash",
                      "training_window", "evaluation_window", "dataset", "metrics", "reproducibility",
                      "evidence", "artifacts"), "experiment")
    if record["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported experiment schema_version")
    if not MODEL_ID.fullmatch(str(record["experiment_id"])):
        raise ValueError("invalid experiment_id")
    if not MODEL_ID.fullmatch(str(record["model_id"])):
        raise ValueError("invalid model_id")
    overall = record["metrics"].get("overall", {})
    for metric in ("brier_score", "log_loss", "ece", "roi"):
        if metric not in overall:
            raise ValueError(f"experiment metrics.overall missing {metric}")
    reproducibility = record["reproducibility"]
    _require(reproducibility, ("seed", "simulations", "network_contacted"), "reproducibility")
    if reproducibility["network_contacted"] is not False:
        raise ValueError("registered experiments must be reproducible offline")


def _record_path(root: Path, kind: str, identifier: str) -> Path:
    return root / kind / f"{identifier}.json"


def _register(root: Path, kind: str, identifier: str, record: dict[str, Any]) -> tuple[Path, str]:
    path = _record_path(root, kind, identifier)
    payload_hash = content_hash(record)
    if path.exists():
        if content_hash(_load(path)) != payload_hash:
            raise FileExistsError(f"immutable {kind} id already contains different content: {identifier}")
        return path, payload_hash
    _write_atomic(path, record)
    return path, payload_hash


def _build_index(root: Path) -> dict[str, Any]:
    def records(kind: str) -> dict[str, Any]:
        result = {}
        for path in sorted((root / kind).glob("*.json")) if (root / kind).exists() else []:
            item = _load(path)
            identifier = str(item["model_id"] if kind == "models" else item["experiment_id"])
            result[identifier] = {"path": path.relative_to(root).as_posix(), "sha256": content_hash(item)}
        return result
    promotions = {}
    for path in sorted((root / "promotions").glob("*.json")) if (root / "promotions").exists() else []:
        item = _load(path)
        promotions[str(item["promotion_id"])] = {"path": path.relative_to(root).as_posix(), "sha256": content_hash(item)}
    return {"schema_version": SCHEMA_VERSION, "models": records("models"),
            "experiments": records("experiments"), "promotions": promotions}


def rebuild_index(root: Path) -> dict[str, Any]:
    index = _build_index(root)
    _write_atomic(root / "index.json", index)
    return index


def register_model(root: Path, record: dict[str, Any]) -> tuple[Path, str]:
    validate_model(record)
    result = _register(root, "models", str(record["model_id"]), record)
    rebuild_index(root)
    return result


def register_experiment(root: Path, record: dict[str, Any]) -> tuple[Path, str]:
    validate_experiment(record)
    model_path = _record_path(root, "models", str(record["model_id"]))
    if not model_path.exists():
        raise FileNotFoundError(f"model must be registered first: {record['model_id']}")
    result = _register(root, "experiments", str(record["experiment_id"]), record)
    rebuild_index(root)
    return result


def promotion_check(experiment: dict[str, Any], baseline_model_id: str) -> dict[str, Any]:
    reasons: list[str] = []
    window = experiment.get("evaluation_window", {})
    dataset = experiment.get("dataset", {})
    evidence = experiment.get("evidence", {})
    comparison = experiment.get("baseline_comparison") or {}
    evaluated_weeks = window.get("evaluated_weeks") or []
    if len(evaluated_weeks) < PROMOTION_POLICY["minimum_evaluated_weeks"]:
        reasons.append("fewer than 15 evaluated weeks")
    if int(dataset.get("opportunities") or 0) < PROMOTION_POLICY["minimum_opportunities"]:
        reasons.append("fewer than 1000 evaluated opportunities")
    if int(dataset.get("independent_games") or 0) < PROMOTION_POLICY["minimum_independent_games"]:
        reasons.append("fewer than 100 independent games")
    if evidence.get("leakage_safe") is not True:
        reasons.append("experiment is not certified leakage-safe")
    if evidence.get("out_of_sample") is not True:
        reasons.append("experiment is not out-of-sample")
    if comparison.get("baseline_model_id") != baseline_model_id:
        reasons.append("missing comparison against the registered baseline")
    if comparison.get("paired_opportunities") is not True:
        reasons.append("baseline comparison is not paired on identical opportunities")
    deltas = comparison.get("metric_deltas") or {}
    for metric in ("brier_score", "log_loss"):
        value = deltas.get(metric) or {}
        if value.get("ci_95") is None or len(value["ci_95"]) != 2 or float(value["ci_95"][1]) >= 0:
            reasons.append(f"{metric} improvement 95% CI does not clear zero")
    roi = deltas.get("roi") or {}
    if roi.get("ci_95") is None or len(roi["ci_95"]) != 2 or float(roi["ci_95"][0]) <= 0:
        reasons.append("ROI improvement 95% CI does not clear zero")
    ece = deltas.get("ece") or {}
    if ece.get("estimate") is None:
        reasons.append("ECE baseline comparison is missing")
    elif float(ece["estimate"]) > 0:
        reasons.append("ECE is worse than baseline")
    return {"eligible": not reasons, "policy": PROMOTION_POLICY, "reasons": reasons}


def promote(root: Path, model_id: str, experiment_id: str, baseline_model_id: str) -> dict[str, Any]:
    model = _load(_record_path(root, "models", model_id))
    experiment = _load(_record_path(root, "experiments", experiment_id))
    if experiment["model_id"] != model["model_id"]:
        raise ValueError("experiment does not belong to candidate model")
    decision = promotion_check(experiment, baseline_model_id)
    if not decision["eligible"]:
        raise ValueError("promotion rejected: " + "; ".join(decision["reasons"]))
    promotion_id = f"{model_id}--{experiment_id}"
    record = {"schema_version": SCHEMA_VERSION, "promotion_id": promotion_id,
              "model_id": model_id, "experiment_id": experiment_id,
              "baseline_model_id": baseline_model_id, "policy": PROMOTION_POLICY,
              "decision": "PROMOTED"}
    _register(root, "promotions", promotion_id, record)
    rebuild_index(root)
    return record


def validate_registry(root: Path) -> dict[str, Any]:
    expected = _build_index(root)
    actual = _load(root / "index.json")
    if actual != expected:
        raise ValueError("registry index is stale or contains invalid hashes")
    for item in expected["models"].values():
        validate_model(_load(root / item["path"]))
    for item in expected["experiments"].values():
        experiment = _load(root / item["path"])
        validate_experiment(experiment)
        if experiment["model_id"] not in expected["models"]:
            raise ValueError(f"experiment references unknown model: {experiment['model_id']}")
        for artifact in experiment["artifacts"].values():
            digest = str(artifact.get("sha256") or "")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("experiment artifact has invalid sha256")
    return {"status": "VALID", "models": len(expected["models"]),
            "experiments": len(expected["experiments"]), "promotions": len(expected["promotions"])}


def best_distribution(root: Path, market: str, *, model_id: str | None = None,
                      allow_experimental: bool = False) -> dict[str, Any]:
    """Resolve a market backend without silently using experimental evidence."""
    index = _load(root / "index.json")
    if model_id is None:
        promoted = []
        for item in index.get("promotions", {}).values():
            record = _load(root / item["path"])
            if record.get("decision") == "PROMOTED": promoted.append(str(record["model_id"]))
        if not promoted:
            raise LookupError("no promoted model supplies an automatic distribution")
        model_id = sorted(promoted)[-1]
    item = index.get("models", {}).get(model_id)
    if item is None:
        raise LookupError(f"unknown registered model: {model_id}")
    model = _load(root / item["path"])
    if model.get("state") == "experimental" and not allow_experimental:
        raise PermissionError(f"experimental distribution requires allow_experimental: {model_id}")
    backends = (model.get("distribution") or {}).get("backends") or {}
    if market not in backends:
        raise LookupError(f"model {model_id} has no distribution for market {market}")
    return {"model_id": model_id, "market": market, "family": backends[market],
            "model_state": model.get("state"), "experimental": model.get("state") == "experimental"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rebuild-index")
    commands.add_parser("validate")
    register_model_parser = commands.add_parser("register-model")
    register_model_parser.add_argument("--definition", type=Path, required=True)
    register_experiment_parser = commands.add_parser("register-experiment")
    register_experiment_parser.add_argument("--result", type=Path, required=True)
    check_parser = commands.add_parser("promotion-check")
    check_parser.add_argument("--experiment-id", required=True)
    check_parser.add_argument("--baseline-model-id", default="nfl_game_baseline_v3")
    promote_parser = commands.add_parser("promote")
    promote_parser.add_argument("--model-id", required=True)
    promote_parser.add_argument("--experiment-id", required=True)
    promote_parser.add_argument("--baseline-model-id", default="nfl_game_baseline_v3")
    distribution_parser = commands.add_parser("best-distribution")
    distribution_parser.add_argument("--market", required=True)
    distribution_parser.add_argument("--model-id")
    distribution_parser.add_argument("--allow-experimental", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "rebuild-index":
        result = rebuild_index(args.root)
    elif args.command == "validate":
        result = validate_registry(args.root)
    elif args.command == "register-model":
        path, digest = register_model(args.root, _load(args.definition))
        result = {"path": str(path), "sha256": digest}
    elif args.command == "register-experiment":
        path, digest = register_experiment(args.root, _load(args.result))
        result = {"path": str(path), "sha256": digest}
    elif args.command == "promotion-check":
        result = promotion_check(_load(_record_path(args.root, "experiments", args.experiment_id)),
                                 args.baseline_model_id)
    elif args.command == "best-distribution":
        result = best_distribution(args.root, args.market, model_id=args.model_id,
                                   allow_experimental=args.allow_experimental)
    else:
        result = promote(args.root, args.model_id, args.experiment_id, args.baseline_model_id)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
