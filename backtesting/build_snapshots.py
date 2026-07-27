"""Build normalized historical snapshot folders for internal backtesting."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR, SNAPSHOTS_DIR
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
    parser.add_argument("--data-dir", type=Path, default=SNAPSHOTS_DIR)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--providers", default="odds-api,espn,nfl-official,local-json")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--check-providers", action="store_true")
    parser.add_argument("--odds-hours-before-kickoff", type=int, default=24)
    parser.add_argument("--require-backtest-ready", action="store_true")
    parser.add_argument("--refresh", choices=("team-stats",), help="Refresh only free statistical history; preserves odds.json and never contacts The Odds API.")
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


def _write_json(path: Path, records: Any, overwrite: bool) -> None:
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


def _manifest(league: str, season: str, week: int, normalized: dict[str, list[dict[str, Any]]], warnings: list[str], source_by_dataset: dict[str, str], leakage_ok: bool) -> dict[str, Any]:
    datasets = {}
    for d in DATASETS:
        count = len(normalized.get(d, []))
        required = d in REQUIRED_DATASETS
        datasets[d] = {
            "source": source_by_dataset.get(d, "none" if not count else "mixed"),
            "records": count,
            "status": "complete" if count else ("missing" if required else "optional_empty"),
        }
        if not count and not required:
            datasets[d]["reason"] = "historical provider unavailable or local export missing"
    generated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    games = normalized.get("games", [])
    cutoffs = {str(g.get("game_id")): min(
        (str(r.get("data_as_of") or r.get("captured_at")) for d in ("odds", "weather", "injuries")
         for r in normalized.get(d, []) if r.get("game_id") == g.get("game_id") and (r.get("data_as_of") or r.get("captured_at"))),
        default=None,
    ) for g in games}
    return {"league": league.lower(), "season": int(season), "week": int(week),
        "generated_at": generated, "created_at": generated, "schema_version": 1,
        "normalization_version": "nfl-historical-v1", "builder_version": "phase2-leakage-safe",
        "cutoff_policy": "Every feature timestamp must be strictly before its game's kickoff; outcomes are grading-only.",
        "prediction_cutoffs": cutoffs, "source_versions": source_by_dataset,
        "source_lineage": {d: {"provider": info["source"], "records": info["records"],
            "original_event_timestamp_field": "captured_at/data_as_of"} for d, info in datasets.items()},
        "datasets": datasets, "warnings": warnings, "leakage_checks_passed": leakage_ok}


def _refresh_manifest_dataset(wdir: Path, dataset: str, rows: list[dict[str, Any]]) -> None:
    """Update only one manifest dataset, preserving every unrelated byte-level value."""
    path = wdir / "manifest.json"
    manifest = json.loads(path.read_text()) if path.exists() else {"datasets": {}}
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    previous = dict((manifest.get("datasets") or {}).get(dataset) or {})
    previous.update({
        "source": next((r.get("source") for r in rows if r.get("source")), "none"),
        "records": len(rows),
        "status": "complete" if rows else "optional_empty",
        "refreshed_at": now,
    })
    manifest.setdefault("datasets", {})[dataset] = previous
    manifest["refreshed_at"] = now
    tmp = wdir / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _provider_capabilities(sources: list[HistoricalSnapshotSource]) -> None:
    import os
    print("Provider capability report (API keys redacted):")
    for s in sources:
        name = getattr(s, "name", "unknown")
        supported = sorted(getattr(s, "supported_datasets", set()))
        configured = "configured" if (name != "odds-api" or os.getenv("THE_ODDS_API_KEY") or os.getenv("ODDS_API_KEY")) else "missing key"
        historical = bool(set(supported) - ({"weather"} if name in {"openweather"} else set()))
        live_only = name in {"openweather", "existing-nfl"}
        paid = name == "odds-api"
        local_fill = name in {"odds-api", "historical-weather", "openweather", "espn"}
        print(f"- provider={name} key={configured} supported={','.join(supported) or 'none'} historical={historical} live_only={live_only} paid_subscription_required={paid} local_export_can_fill_gap={local_fill}")


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
    source_by_dataset: dict[str, str] = {}
    all_warnings: list[str] = []
    games, warnings = _fetch_dataset(sources, cache, "games", args.league, args.season, week, week_range, [])
    all_warnings.extend(warnings)
    raw["games"] = games
    source_by_dataset["games"] = next((r.get("source") for r in games if r.get("source")), "unknown") if games else "none"
    for dataset in DATASETS[1:]:
        rows, warnings = _fetch_dataset(sources, cache, dataset, args.league, args.season, week, week_range, games)
        raw[dataset] = rows
        source_by_dataset[dataset] = next((r.get("source") for r in rows if r.get("source")), "none") if rows else "none"
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
        tmp_report = validate_snapshot(args.data_dir, args.league, args.season, [week], strict=args.strict, require_backtest_ready=getattr(args, "require_backtest_ready", False))
        metadata = _manifest(args.league, args.season, week, normalized, all_warnings, source_by_dataset, tmp_report.ok)
        _write_json(wdir / "manifest.json", metadata, True)
        _write_json(wdir / "metadata.json", metadata, True)
    for dataset in DATASETS:
        label = dataset.replace("_", " ").title()
        print(f"- {label} records: {len(normalized[dataset])}")
    for warning in all_warnings:
        print(f"WARNING: {warning}")
    ok = True
    if args.validate and not args.dry_run:
        report = validate_snapshot(args.data_dir, args.league, args.season, [week], strict=args.strict, require_backtest_ready=getattr(args, "require_backtest_ready", False))
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
    if getattr(args, "refresh", None) == "team-stats":
        # Deliberately construct no odds source. Games are read from the local
        # snapshot and only team_stats.json is replaced.
        sources = create_sources("espn")
        failed = 0
        for week in range(args.start_week, args.end_week + 1):
            wdir = snapshot_week_dir(args.data_dir, args.league, args.season, week)
            try:
                games = json.loads((wdir / "games.json").read_text())
                source = next(s for s in sources if "team_stats" in s.supported_datasets)
                rows = source.fetch_team_stats(args.league, args.season, week, nfl_week_date_range(args.season, week), games)
                normalized = normalize_dataset("team_stats", rows, args.league, args.season, week)
                if not normalized:
                    raise SnapshotError("ESPN returned no completed prior team history")
                tmp = wdir / "team_stats.json.tmp"
                tmp.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n")
                tmp.replace(wdir / "team_stats.json")
                _refresh_manifest_dataset(wdir, "team_stats", normalized)
                print(f"NFL {args.season} Week {week}: team history records={len(normalized)}")
                if args.validate:
                    report = validate_snapshot(args.data_dir, args.league, args.season, [week])
                    if not report.ok:
                        raise SnapshotError("; ".join(report.errors))
            except Exception as exc:
                failed += 1; print(f"ERROR: {_redact(str(exc))}")
        print("Historical odds preserved; Odds API not requested.")
        return 1 if failed else 0

    try:
        sources = create_sources(args.providers, getattr(args, "odds_hours_before_kickoff", 24))
    except TypeError:
        sources = create_sources(args.providers)
    if getattr(args, "check_providers", False):
        _provider_capabilities(sources)
        return 0
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
