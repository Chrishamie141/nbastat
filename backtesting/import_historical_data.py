"""Import and validate internal historical snapshots for backtesting."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import DATA_DIR
from .snapshots import DATASETS, SnapshotError, load_source, normalize_dataset, snapshot_week_dir, validate_snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import local historical backtesting snapshots.")
    parser.add_argument("--league", required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--week", type=int)
    parser.add_argument("--source")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "snapshots")
    return parser.parse_args()


def _print_report(report) -> None:
    print("Validation summary:")
    for key, value in sorted(report.counts.items()):
        print(f"- {key}: {value}")
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    for error in report.errors:
        print(f"ERROR: {error}")


def main() -> None:
    args = parse_args()
    if args.validate_only:
        report = validate_snapshot(args.data_dir, args.league, args.season, [args.week] if args.week else None)
        _print_report(report)
        raise SystemExit(0 if report.ok else 1)
    if not args.source or args.week is None:
        raise SystemExit("--source and --week are required unless --validate-only is supplied")
    raw = load_source(Path(args.source), args.format)
    out_dir = snapshot_week_dir(args.data_dir, args.league, args.season, args.week)
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for dataset in DATASETS:
        records = normalize_dataset(dataset, raw.get(dataset, []), args.league, args.season, args.week)
        path = out_dir / f"{dataset}.json"
        if path.exists() and not args.overwrite:
            raise SnapshotError(f"Refusing to overwrite existing snapshot without --overwrite: {path}")
        path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")
        counts[dataset] = len(records)
    print(f"Imported {args.league.upper()} {args.season} Week {args.week} snapshots to {out_dir}")
    for name in DATASETS:
        print(f"- {name}: {counts[name]}")
    report = validate_snapshot(args.data_dir, args.league, args.season, [args.week])
    _print_report(report)
    raise SystemExit(0 if report.ok else 1)


if __name__ == "__main__":
    main()
