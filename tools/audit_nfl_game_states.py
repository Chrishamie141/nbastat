"""Operator command for deterministic NFL lifecycle reconciliation audits.

Examples:
  py -3.14 tools/audit_nfl_game_states.py --game-id 401000001
  py -3.14 tools/audit_nfl_game_states.py --game-id 401000001 --fixture tests/fixtures/nfl_game_detail_completed_preseason.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.nfl_game_service import get_nfl_game_detail


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and reconcile authoritative NFL game lifecycle state.")
    parser.add_argument("--game-id", action="append", required=True, help="Canonical ESPN game ID; repeat for multiple games.")
    parser.add_argument("--fixture", type=Path, help="Optional deterministic provider-summary fixture (never contacts the network).")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "nfl_game_state_audit.json")
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8")) if args.fixture else None
    rows = []
    for game_id in args.game_id:
        detail = get_nfl_game_detail(game_id, force=True, trigger="operator_audit", fetcher=(lambda _game_id: fixture) if fixture else None)
        rows.append({
            "gameId": detail["game"]["canonicalId"],
            "teams": {"away": detail["game"]["awayTeam"]["abbreviation"], "home": detail["game"]["homeTeam"]["abbreviation"]},
            "kickoff": detail["game"]["startTimeUtc"],
            "status": detail["game"]["status"],
            "phaseWeekKey": detail["game"]["phaseWeekKey"],
            "providerStatus": detail["lifecycle"]["providerStatus"],
            "providerUpdatedAt": detail["game"]["statusUpdatedAt"],
            "lastLocalRefresh": detail["lifecycle"]["fetchedAt"],
            "scoreAvailable": detail["actuals"]["scoreAvailable"],
            "playerStatsAvailable": any(group["items"] for group in detail["actuals"]["playerGroups"]),
            "suspectedIssue": detail["reconciliation"]["reasonCodes"],
            "result": "REPAIRED" if detail["reconciliation"]["repaired"] else "REVIEW" if detail["reconciliation"]["stale"] else "OK",
            **detail["reconciliation"],
        })
    artifact = {
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": "fixture" if fixture else "espn_summary",
        "paidProviderContacted": False,
        "games": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(artifact, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
