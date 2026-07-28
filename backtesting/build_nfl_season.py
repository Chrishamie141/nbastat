"""Prepare, plan, and validate a partitioned NFL regular season."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .build_snapshots import main as build_snapshots
from .config import SNAPSHOTS_DIR
from .nfl_season import (execute_grouped_odds, plan_season, season_coverage,
                         season_registry, write_json_atomic)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", required=True, type=int)
    parser.add_argument("--start-week", type=int, default=1)
    parser.add_argument("--end-week", type=int, default=18)
    parser.add_argument("--data-dir", type=Path, default=SNAPSHOTS_DIR)
    parser.add_argument("--results-dir", type=Path, default=Path("backtesting/results"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--prepare", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--allow-paid-odds-fetch", action="store_true")
    parser.add_argument("--odds-hours-before-kickoff", type=int, default=24)
    parser.add_argument("--grouping-tolerance-minutes", type=int, default=0)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    common = ["--league", "nfl", "--season", str(args.season), "--start-week", str(args.start_week),
              "--end-week", str(args.end_week), "--data-dir", str(args.data_dir), "--resume",
              "--odds-hours-before-kickoff", str(args.odds_hours_before_kickoff)]
    # Free preparation is isolated from the paid adapter by provider selection.
    prepare_status = 0
    if args.prepare and args.plan:
        # build_snapshots' plan path discovers missing schedules through ESPN
        # but returns before constructing or calling an odds provider.
        prepare_status = build_snapshots(common + ["--providers", "espn,local-json", "--plan"])
    elif args.prepare:
        prepare_status = build_snapshots(common + ["--providers", "espn,local-json"] + (["--validate"] if args.validate else []))

    weeks = range(args.start_week, args.end_week + 1)
    plan = plan_season(args.data_dir, args.season, weeks, hours_before=args.odds_hours_before_kickoff,
                       tolerance_minutes=args.grouping_tolerance_minutes)
    print(json.dumps(plan, indent=2, sort_keys=True))
    if args.plan:
        print("SAFE PLAN: no paid Odds API requests were made.")
    elif plan["totals"]["paid_requests"] and not args.allow_paid_odds_fetch:
        print("Paid odds work blocked. Rerun with --allow-paid-odds-fetch after reviewing this exact plan.")
        prepare_status = prepare_status or 2
    elif plan["totals"]["naive_request_count"]:
        diagnostics = execute_grouped_odds(args.data_dir, args.season, weeks,
            hours_before=args.odds_hours_before_kickoff,
            tolerance_minutes=args.grouping_tolerance_minutes)
        print("Grouped odds reconciliation: " + json.dumps(diagnostics, sort_keys=True))
        prepare_status = build_snapshots(common + ["--providers", "espn,local-json",
                                                    "--validate"])

    registry = season_registry(args.data_dir, args.season, weeks)
    write_json_atomic(args.results_dir / f"nfl_{args.season}_season_games.json", registry)
    coverage = season_coverage(args.data_dir, args.season, weeks)
    write_json_atomic(args.results_dir / f"nfl_{args.season}_season_coverage.json", coverage)
    print(f"Season: weeks={len(coverage['weeks'])} games={len(registry)} status={coverage['status']} "
          f"validation_issues={sum(len(w['issues']) for w in coverage['weeks'])}")
    return prepare_status


if __name__ == "__main__":
    raise SystemExit(main())
