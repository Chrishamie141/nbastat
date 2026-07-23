"""Build normalized historical snapshot folders for internal backtesting."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .snapshot_sources import DATASET_METHODS, HistoricalSnapshotSource, ProviderUnavailable, RawCache, create_sources, _redact
from .snapshots import DATASETS, REQUIRED_DATASETS, SnapshotError, normalize_dataset, snapshot_week_dir, validate_snapshot

OPTIONAL_DATASETS = tuple(d for d in DATASETS if d not in REQUIRED_DATASETS)


@dataclass
class BuildSummary:
    requested: int = 0
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    missing: dict[str, list[int]] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=lambda: {d: 0 for d in DATASETS})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build internal historical backtesting snapshots.")
    parser.add_argument("--league", required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--start-week", type=int, required=True)
    parser.add_argument("--end-week", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "snapshots")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--providers", default="local-json,existing-nfl")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def nfl_week_date_range(season: str | int, week: int) -> tuple[str, str]:
    """Return the Thursday-to-Wednesday range for an NFL regular-season week."""
    starts = {2025: date(2025, 9, 4)}
    start = starts.get(int(season))
    if not start:
        # NFL week 1 typically begins the first Thursday after Labor Day.
        sep1 = date(int(season), 9, 1)
        start = sep1 + timedelta(days=(3 - sep1.weekday()) % 7)
    week_start = start + timedelta(days=(int(week) - 1) * 7)
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return week_start.isoformat(), week_end.isoformat()


def _complete_valid(data_dir: Path, league: str, season: str, week: int) -> bool:
    wdir = snapshot_week_dir(data_dir, league, season, week)
    return all((wdir / f"{d}.json").exists() for d in DATASETS) and validate_snapshot(data_dir, league, season, [week]).ok


def _write_json(path: Path, records: list[dict[str, Any]], overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise SnapshotError(f"Refusing to overwrite existing snapshot without --overwrite: {path}")
    path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")


def _fetch_dataset(sources: list[HistoricalSnapshotSource], cache: RawCache, dataset: str, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    method_name = DATASET_METHODS[dataset]
    for source in sources:
        if dataset not in getattr(source, "supported_datasets", set()):
            continue
        try:
            def call():
                method = getattr(source, method_name)
                if dataset == "games":
                    return method(league, season, week, week_range)
                return method(league, season, week, week_range, games)
            return cache.get_or_fetch(source.name, league, season, week, dataset, call), warnings
        except ProviderUnavailable as exc:
            warnings.append(_redact(f"{source.name}: {exc}"))
    return [], warnings or [f"no configured provider supports historical {dataset}"]


def build_week(args: argparse.Namespace, sources: list[HistoricalSnapshotSource], cache: RawCache, week: int) -> tuple[bool, dict[str, int], list[str]]:
    if args.resume and _complete_valid(args.data_dir, args.league, args.season, week):
        print(f"{args.league.upper()} {args.season} Week {week}: skipped complete valid snapshot")
        return True, {d: 0 for d in DATASETS}, []
    wdir = snapshot_week_dir(args.data_dir, args.league, args.season, week)
    if wdir.exists() and any((wdir / f"{d}.json").exists() for d in DATASETS) and not (args.overwrite or args.resume or args.dry_run):
        raise SnapshotError(f"Refusing to overwrite existing week without --overwrite or --resume: {wdir}")

    week_range = nfl_week_date_range(args.season, week)
    print(f"{args.league.upper()} {args.season} Week {week}")
    print(f"- Week range: {week_range[0]} to {week_range[1]}")
    raw: dict[str, list[dict[str, Any]]] = {}
    all_warnings: list[str] = []
    games, warnings = _fetch_dataset(sources, cache, "games", args.league, args.season, week, week_range, [])
    all_warnings.extend(warnings)
    raw["games"] = games
    for dataset in DATASETS[1:]:
        rows, warnings = _fetch_dataset(sources, cache, dataset, args.league, args.season, week, week_range, games)
        raw[dataset] = rows
        all_warnings.extend(warnings)

    normalized = {d: normalize_dataset(d, raw.get(d, []), args.league, args.season, week) for d in DATASETS}
    if args.strict:
        missing = [d for d in DATASETS if not normalized[d]]
    else:
        missing = [d for d in REQUIRED_DATASETS if not normalized[d]]
    if missing:
        all_warnings.append(f"missing required coverage: {', '.join(missing)}")
        if args.strict:
            raise SnapshotError(all_warnings[-1])

    if not args.dry_run:
        wdir.mkdir(parents=True, exist_ok=True)
        for dataset in DATASETS:
            path = wdir / f"{dataset}.json"
            if not normalized[dataset] and dataset in OPTIONAL_DATASETS and not args.strict:
                _write_json(path, [], args.overwrite or args.resume)
            else:
                _write_json(path, normalized[dataset], args.overwrite or args.resume)
    for dataset in DATASETS:
        label = dataset.replace("_", " ").title()
        print(f"- {label} records: {len(normalized[dataset])}")
    for warning in all_warnings:
        print(f"WARNING: {warning}")
    ok = True
    if args.validate and not args.dry_run:
        report = validate_snapshot(args.data_dir, args.league, args.season, [week])
        ok = report.ok
        print(f"- Validation: {'passed' if ok else 'failed'}")
        for error in report.errors:
            print(f"ERROR: {error}")
        if args.strict and not ok:
            raise SnapshotError("snapshot validation failed")
    elif args.dry_run:
        print("- Validation: skipped (dry-run)")
    return ok, {d: len(normalized[d]) for d in DATASETS}, all_warnings


def main(argv: list[str] | argparse.Namespace | None = None) -> int:
    args = argv if isinstance(argv, argparse.Namespace) else parse_args(argv)
    sources = create_sources(args.providers)
    cache = RawCache(Path(args.data_dir).parent / "raw_cache", overwrite=args.overwrite)
    summary = BuildSummary(requested=args.end_week - args.start_week + 1)
    for week in range(args.start_week, args.end_week + 1):
        try:
            if args.resume and _complete_valid(args.data_dir, args.league, args.season, week):
                summary.skipped += 1
                print(f"{args.league.upper()} {args.season} Week {week}: skipped complete valid snapshot")
                continue
            ok, counts, warnings = build_week(args, sources, cache, week)
            summary.completed += int(ok)
            summary.failed += int(not ok)
            for dataset, count in counts.items():
                summary.totals[dataset] += count
            for warning in warnings:
                if "missing" in warning or "no configured" in warning or "do not expose" in warning:
                    summary.missing.setdefault(warning, []).append(week)
        except Exception as exc:
            summary.failed += 1
            print(f"ERROR: {_redact(str(exc))}")
            if args.strict:
                break
    print("Season summary:")
    print(f"- Weeks requested: {summary.requested}")
    print(f"- Weeks completed: {summary.completed}")
    print(f"- Weeks skipped: {summary.skipped}")
    print(f"- Weeks failed: {summary.failed}")
    print(f"- Missing datasets: {len(summary.missing)}")
    for dataset, total in summary.totals.items():
        print(f"- Total {dataset}: {total}")
    print(f"- Snapshot directory: {args.data_dir}")
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
