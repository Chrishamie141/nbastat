"""Small, non-destructive production operations CLI.

Commands:
  app-health              Print database/artifact readiness.
  audit-endpoints         Exercise safe API reads and write an endpoint matrix.
  audit-data-fallbacks    Validate a current-season NFL detail fallback.
  audit-stale-games       Delegate to the canonical stale-game audit.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS = BASE_DIR / "reports"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def app_health() -> int:
    from backend.app.services.readiness_service import readiness_report
    report = readiness_report()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] != "unhealthy" else 1


def _frontend_consumers(path: str) -> list[str]:
    needle = path.replace("{game_id}", "")
    consumers = []
    for file in (BASE_DIR / "frontend").rglob("*.js*"):
        if ".next" in file.parts or "node_modules" in file.parts:
            continue
        try:
            if needle.rstrip("/") in file.read_text(encoding="utf-8"):
                consumers.append(str(file.relative_to(BASE_DIR)))
        except OSError:
            continue
    return consumers


def audit_endpoints() -> int:
    from backend.app.main import app
    openapi = app.openapi()
    safe_paths = {
        "/api/health": "/api/health",
        "/api/readiness": "/api/readiness",
        "/api/config/status": "/api/config/status",
        "/api/sports-mode": "/api/sports-mode",
        "/api/teams": "/api/teams?league=nfl",
        "/api/search": "/api/search?q=patriots",
        "/api/games/upcoming": "/api/games/upcoming?league=nfl&limit=2&include_completed=true",
        "/api/games/featured": "/api/games/featured",
        "/api/nfl/games/{game_id}": "/api/nfl/games/401873271",
    }
    matrix = []
    with TestClient(app) as client:
        for path, operations in sorted(openapi["paths"].items()):
            for method, operation in operations.items():
                if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    continue
                started = time.perf_counter()
                observed = None
                failure = None
                if method == "get" and path in safe_paths:
                    try:
                        response = client.get(safe_paths[path])
                        observed = response.status_code
                        if response.status_code >= 400:
                            failure = response.json()
                    except Exception as exc:  # audit must record rather than conceal failure
                        failure = {"type": type(exc).__name__, "message": str(exc)}
                classification = "HEALTHY" if observed and observed < 400 else "BROKEN" if observed and observed >= 500 else "DEGRADED" if observed is not None or _frontend_consumers(path) else "UNUSED"
                matrix.append({
                    "method": method.upper(),
                    "path": path,
                    "purpose": operation.get("summary"),
                    "authenticationRequirement": "subscription/session" if path.startswith(("/api/dashboard", "/api/history", "/api/performance", "/api/analyze", "/api/billing")) or method != "get" else "public",
                    "requestSchema": operation.get("requestBody") or operation.get("parameters") or [],
                    "responseSchema": operation.get("responses") or {},
                    "frontendConsumer": _frontend_consumers(path),
                    "databaseDependency": path.startswith(("/api/auth", "/api/billing", "/api/dashboard", "/api/history", "/api/performance", "/api/analyze")),
                    "externalDataDependency": path.startswith(("/api/games", "/api/nfl/games", "/api/search")),
                    "expectedStatusCodes": sorted((operation.get("responses") or {}).keys()),
                    "observedStatus": observed,
                    "latencyMs": round((time.perf_counter() - started) * 1000, 1),
                    "classification": classification,
                    "failureReason": failure,
                    "auditNote": None if observed is not None else "Mutation or authenticated route inventoried but not invoked by the non-destructive audit.",
                })
    REPORTS.mkdir(exist_ok=True)
    target = REPORTS / "production_endpoint_matrix.json"
    target.write_text(json.dumps({"generatedBy": "tools/production_readiness.py audit-endpoints", "endpoints": matrix}, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(target), "endpointCount": len(matrix), "safeReadsExercised": sum(row["observedStatus"] is not None for row in matrix)}, indent=2))
    return 1 if any(row["classification"] == "BROKEN" for row in matrix) else 0


def audit_data_fallbacks() -> int:
    from backend.app.services.nfl_game_service import clear_nfl_game_cache, get_nfl_game_detail
    clear_nfl_game_cache()
    detail = get_nfl_game_detail("401873272")
    context = detail["teamContext"]
    result = {"gameId": detail["game"]["id"], "status": detail["game"]["status"], "actualsAvailable": detail["actuals"]["available"], "teamContext": context}
    print(json.dumps(result, indent=2))
    return 0 if context.get("fallbackUsed") and not detail["actuals"]["available"] else 1


def audit_actions() -> int:
    rows = []
    pattern = re.compile(r"<(button|Link)[^>]*(?:href=\"([^\"]+)\")?[^>]*>([^<{]{2,60})")
    for file in (BASE_DIR / "frontend").rglob("*.jsx"):
        if ".next" in file.parts or "node_modules" in file.parts:
            continue
        source = file.read_text(encoding="utf-8")
        for match in pattern.finditer(source):
            label = " ".join(match.group(3).split())
            rows.append({"component": str(file.relative_to(BASE_DIR)), "element": match.group(1), "label": label, "href": match.group(2), "accessibility": "native button/link semantics", "inventorySource": "static JSX audit"})
    target = REPORTS / "production_action_inventory.json"
    target.write_text(json.dumps({"actions": rows}, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(target), "actionCount": len(rows)}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("app-health", "audit-endpoints", "audit-data-fallbacks", "audit-stale-games", "audit-actions"))
    command = parser.parse_args().command
    if command == "app-health": return app_health()
    if command == "audit-endpoints": return audit_endpoints()
    if command == "audit-data-fallbacks": return audit_data_fallbacks()
    if command == "audit-actions": return audit_actions()
    return subprocess.call([sys.executable, str(BASE_DIR / "tools" / "audit_nfl_game_states.py"), "--game-id", "401873271", "--game-id", "401873272"], cwd=BASE_DIR)


if __name__ == "__main__":
    raise SystemExit(main())
