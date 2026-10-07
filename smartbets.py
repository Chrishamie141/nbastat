"""SmartBetSports operational command line."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.services.nfl_production_service import audit_week
from backtesting.nba_replay import replay_dataset


def _print_audit(report: dict) -> None:
    print(report["title"])
    print()
    for check in report["checks"]:
        dots = "." * max(2, 35 - len(check["name"]))
        print(f"{check['name']}{dots} {check['status']}")
    counts = report["counts"]
    print()
    for label, key in (
        ("Final games", "finalGames"), ("Final predictions graded", "finalPredictionsGraded"),
        ("Correct predictions", "correctPredictions"), ("Incorrect predictions", "incorrectPredictions"),
        ("Benchmark SGPs", "benchmarkSgps"), ("SGPs graded", "sgpsGraded"),
        ("SGPs pending", "sgpsPending"), ("NO_BET", "noBet"),
    ):
        print(f"{label}{'.' * max(2, 35 - len(label))} {counts[key]}")
    print()
    print(f"Critical errors{'.' * 20} {len(report['criticalErrors'])}")
    print(f"Warnings{'.' * 27} {len(report['warnings'])}")
    print(f"OVERALL{'.' * 28} {report['result']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smartbets")
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit", help="Run a production-integrity audit")
    audit.add_argument("--league", default="NFL", choices=["NFL"])
    audit.add_argument("--season", type=int, required=True)
    audit.add_argument("--season-type", default="regular")
    audit.add_argument("--week", type=int, required=True)
    audit.add_argument("--json", action="store_true")
    nba = sub.add_parser("nba-replay", help="Run a chronological offline NBA replay")
    nba.add_argument("--dataset-dir", type=Path, required=True)
    nba.add_argument("--season", required=True)
    nba.add_argument("--model-version", default="nba-player-stat-v1")
    nba.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "nba-replay":
        summary = replay_dataset(dataset_dir=args.dataset_dir, season=args.season,
                                 model_version=args.model_version, output=args.output)
        print(json.dumps(summary["metrics"], indent=2, sort_keys=True))
        return 0
    report = audit_week(season=args.season, season_type=args.season_type, week=args.week)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_audit(report)
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
