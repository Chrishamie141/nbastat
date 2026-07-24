"""CLI wrapper for snapshot validation."""
from __future__ import annotations
import argparse
from pathlib import Path
from .config import DATA_DIR
from .snapshots import validate_snapshot

def parse_args(argv=None):
    p=argparse.ArgumentParser(description="Validate historical backtesting snapshots.")
    p.add_argument("--league", required=True); p.add_argument("--season", required=True)
    p.add_argument("--start-week", type=int); p.add_argument("--end-week", type=int)
    p.add_argument("--data-dir", type=Path, default=DATA_DIR / "snapshots")
    p.add_argument("--strict", action="store_true"); p.add_argument("--require-backtest-ready", action="store_true")
    return p.parse_args(argv)

def main(argv=None):
    a=parse_args(argv); weeks=None
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
