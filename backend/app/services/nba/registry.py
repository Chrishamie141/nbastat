"""NBA-only model registry and conservative promotion governance."""
from __future__ import annotations

from datetime import datetime, timezone
import json

from backend.app.database import get_db_connection, using_postgres

NBA_BASELINE_VERSION = "nba-player-stat-v1"
MIN_VALIDATION_PREDICTIONS = 500


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def initialize_registry() -> None:
    with get_db_connection() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS nba_model_registry (
            model_version TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL,
            evaluated_at TEXT, evaluation_start TEXT, evaluation_end TEXT,
            sample_size INTEGER NOT NULL DEFAULT 0, metrics_json TEXT NOT NULL DEFAULT '{}',
            production_eligible INTEGER NOT NULL DEFAULT 0,
            promotion_blockers_json TEXT NOT NULL DEFAULT '[]', notes TEXT)""")
        connection.execute("""INSERT INTO nba_model_registry
            (model_version,status,created_at,sample_size,metrics_json,production_eligible,
             promotion_blockers_json,notes) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(model_version) DO NOTHING""",
            (NBA_BASELINE_VERSION, "RESEARCH_ONLY", _now(), 0, "{}", 0,
             json.dumps(["chronological_validation_required", "minimum_sample_not_met",
                         "calibration_not_established", "profitability_not_established"]),
             "Infrastructure baseline; not an approved production model."))
        if using_postgres():
            connection.execute("ALTER TABLE nba_model_registry ENABLE ROW LEVEL SECURITY")
            connection.execute("REVOKE ALL ON TABLE nba_model_registry FROM anon, authenticated")


def record_evaluation(*, model_version: str, metrics: dict, evaluation_start: str | None,
                      evaluation_end: str | None) -> dict:
    """Record evidence without automatically promoting a model."""
    if not str(model_version).startswith("nba-"):
        raise ValueError("NBA registry accepts NBA-owned model versions only")
    initialize_registry()
    sample = int(metrics.get("predictionsEvaluated") or metrics.get("total") or 0)
    blockers = []
    if sample < MIN_VALIDATION_PREDICTIONS:
        blockers.append("minimum_sample_not_met")
    if metrics.get("brierScore") is None or metrics.get("logLoss") is None:
        blockers.append("calibration_not_established")
    if metrics.get("validWagerCount", 0) < 100 or metrics.get("roi") is None or metrics.get("roi") <= 0:
        blockers.append("positive_forward_roi_not_established")
    # Passing numerical gates is necessary but never sufficient: promotion is
    # a separate reviewed action that this service intentionally does not offer.
    blockers.append("manual_promotion_review_required")
    with get_db_connection() as connection:
        connection.execute("""INSERT INTO nba_model_registry
            (model_version,status,created_at,evaluated_at,evaluation_start,evaluation_end,
             sample_size,metrics_json,production_eligible,promotion_blockers_json)
            VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(model_version) DO UPDATE SET
              evaluated_at=excluded.evaluated_at,evaluation_start=excluded.evaluation_start,
              evaluation_end=excluded.evaluation_end,sample_size=excluded.sample_size,
              metrics_json=excluded.metrics_json,production_eligible=0,
              promotion_blockers_json=excluded.promotion_blockers_json""",
            (model_version, "RESEARCH_ONLY", _now(), _now(), evaluation_start, evaluation_end,
             sample, json.dumps(metrics, sort_keys=True), 0, json.dumps(blockers)))
    return {"modelVersion": model_version, "status": "RESEARCH_ONLY",
            "productionEligible": False, "promotionBlockers": blockers}


def registry_status() -> dict:
    initialize_registry()
    with get_db_connection() as connection:
        rows = connection.execute("SELECT * FROM nba_model_registry ORDER BY created_at,model_version").fetchall()
    return {"productionReady": False, "champion": None, "models": [{
        "modelVersion": row["model_version"], "status": row["status"],
        "sampleSize": row["sample_size"], "evaluatedAt": row["evaluated_at"],
        "metrics": json.loads(row["metrics_json"] or "{}"),
        "productionEligible": bool(row["production_eligible"]),
        "promotionBlockers": json.loads(row["promotion_blockers_json"] or "[]"),
    } for row in rows]}
