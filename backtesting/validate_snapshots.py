"""CLI wrapper for snapshot validation."""
from __future__ import annotations
import argparse
from pathlib import Path
from .config import SNAPSHOTS_DIR
from .snapshots import validate_snapshot
from .snapshot_coverage import coverage

def parse_args(argv=None):
    p=argparse.ArgumentParser(description="Validate historical backtesting snapshots.")
    p.add_argument("--league"); p.add_argument("--season")
    p.add_argument("--sport", help="Discover and summarize every season for this sport")
    p.add_argument("--start-week", type=int); p.add_argument("--end-week", type=int)
    p.add_argument("--data-dir", type=Path, default=SNAPSHOTS_DIR)
    p.add_argument("--strict", action="store_true"); p.add_argument("--require-backtest-ready", action="store_true")
    return p.parse_args(argv)

def main(argv=None):
    a=parse_args(argv)
    if a.sport:
        report = coverage(a.data_dir, a.sport)
        print(f"Seasons discovered: {len(report['available_seasons'])}")
        print(f"Weeks discovered: {report['weeks_discovered']}")
        print(f"Games discovered: {report['games_discovered']}")
        print(f"Valid games: {report['games_successfully_snapshotted']}")
        print(f"Invalid games: {report['invalid_games']}")
        for code, count in report["exclusion_reason_counts"].items(): print(f"- {code}: {count}")
        return 0 if report["invalid_games"] == 0 else 1
    if not a.league or not a.season:
        raise SystemExit("--league and --season are required unless --sport is used")
    weeks=None
    if a.start_week is not None:
        weeks=list(range(a.start_week, (a.end_week or a.start_week)+1))
    r=validate_snapshot(a.data_dir, a.league, a.season, weeks, strict=a.strict, require_backtest_ready=a.require_backtest_ready)
    print(f"Validation: {'passed' if r.ok else 'failed'}")
    for k,v in sorted(r.counts.items()): print(f"- {k}: {v}")
    for w in r.warnings: print(f"WARNING: {w}")
    for e in r.errors: print(f"ERROR: {e}")
    return 0 if r.ok else 1
if __name__ == "__main__":
    raise SystemExit(main())
