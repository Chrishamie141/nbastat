"""Convenience CLI for reproducible NFL historical snapshot construction."""
from __future__ import annotations
import argparse
from pathlib import Path

from .build_snapshots import main as build
from .config import SNAPSHOTS_DIR
from .snapshot_coverage import coverage, write_coverage

ROOT = Path(__file__).resolve().parents[1]


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Build leakage-safe historical NFL snapshots.")
    p.add_argument("--season", action="append", type=int, required=True)
    p.add_argument("--week", action="append", type=int)
    p.add_argument("--start-week", type=int)
    p.add_argument("--end-week", type=int)
    p.add_argument("--output-dir", type=Path, default=SNAPSHOTS_DIR)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--validate-only", action="store_true")
    p.add_argument("--providers", default="odds-api,espn,nfl-official,local-json")
    p.add_argument("--odds-hours-before-kickoff", type=int, default=24)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--validate", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--allow-paid-odds-fetch", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv); result = 0
    if not args.validate_only:
        weeks = sorted(set(args.week or range(args.start_week or 1, (args.end_week or 22) + 1)))
        for season in sorted(set(args.season)):
            for week in weeks:
                command = ["--league", "nfl", "--season", str(season), "--start-week", str(week),
                    "--end-week", str(week), "--data-dir", str(args.output_dir), "--providers", args.providers,
                    "--odds-hours-before-kickoff", str(args.odds_hours_before_kickoff), "--validate"]
                command.append("--overwrite" if args.overwrite else "--resume")
                if args.dry_run: command.append("--dry-run")
                if args.allow_paid_odds_fetch: command.append("--allow-paid-odds-fetch")
                result |= build(command)
    report = coverage(args.output_dir, "nfl", args.season)
    write_coverage(report, ROOT / "reports/nfl_historical_snapshot_coverage.json",
                   ROOT / "docs/nfl-historical-snapshot-coverage.md")
    print(f"Seasons discovered: {len(report['available_seasons'])}")
    print(f"Weeks discovered: {report['weeks_discovered']}")
    print(f"Games discovered: {report['games_discovered']}")
    print(f"Valid games: {report['games_successfully_snapshotted']}")
    print(f"Invalid games: {report['invalid_games']}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
