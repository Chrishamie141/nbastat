"""Snapshot path, schema, import, and validation helpers for internal backtesting."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATASETS = ("games", "odds", "weather", "injuries", "player_stats", "team_stats", "outcomes")
REQUIRED_DATASETS = ("games", "outcomes")
SUPPORTED_MARKETS = {"moneyline", "spread", "total", "player_prop", "PASS_YDS", "RUSH_YDS", "REC_YDS", "RECEPTIONS", "TD", "PASS_TD"}

SCHEMAS: dict[str, tuple[str, ...]] = {
    "games": ("game_id", "league", "season", "week", "kickoff_time", "home_team", "away_team", "venue", "status"),
    "odds": ("game_id", "market", "selection", "line", "odds", "sportsbook", "captured_at"),
    "weather": ("game_id", "captured_at", "temperature", "wind_speed", "precipitation", "conditions"),
    "injuries": ("team", "player", "position", "status", "captured_at"),
    "player_stats": ("player", "team", "season", "through_week", "stats"),
    "team_stats": ("team", "season", "through_week", "stats"),
    "outcomes": ("game_id", "final_home_score", "final_away_score", "player_results", "market_results", "completed_at"),
}


class SnapshotError(RuntimeError):
    """Raised when historical snapshots are absent or malformed."""


def week_dir_name(week: int | str) -> str:
    return f"week_{int(week):02d}"


def snapshot_week_dir(root: Path, league: str, season: str | int, week: int | str) -> Path:
    return Path(root) / league.lower() / str(season) / week_dir_name(week)


def snapshot_path(root: Path, league: str, season: str | int, week: int | str, dataset: str) -> Path:
    return snapshot_week_dir(root, league, season, week) / f"{dataset}.json"


def _coerce(value: Any) -> Any:
    if value == "":
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return value
        for caster in (int, float):
            try:
                return caster(text)
            except ValueError:
                pass
    return value


def normalize_dataset(name: str, records: list[dict[str, Any]], league: str, season: str, week: int) -> list[dict[str, Any]]:
    fields = SCHEMAS[name]
    normalized: list[dict[str, Any]] = []
    for record in records:
        row = {field: _coerce(record.get(field)) for field in fields if field in record}
        # Common aliases from raw provider exports.
        if name in {"games", "odds", "weather", "outcomes"} and not row.get("game_id"):
            row["game_id"] = record.get("id") or record.get("game")
        if name == "games":
            row.setdefault("league", league.lower())
            row.setdefault("season", str(season))
            row.setdefault("week", int(week))
            for extra in ("players", "game_type"):
                if extra in record:
                    row[extra] = record[extra]
        if name == "odds" and "market" in row:
            row["market"] = str(row["market"])
        if name in {"player_stats", "team_stats"}:
            row.setdefault("season", str(season))
            row.setdefault("through_week", int(week) - 1)
        normalized.append(row)
    return normalized


def load_source(path: Path, fmt: str) -> dict[str, list[dict[str, Any]]]:
    if fmt == "json":
        payload = json.loads(Path(path).read_text())
        if isinstance(payload, list):
            return {"games": payload}
        return {name: list(payload.get(name, [])) for name in DATASETS}
    if fmt == "csv":
        rows = list(csv.DictReader(Path(path).open(newline="")))
        grouped = {name: [] for name in DATASETS}
        for row in rows:
            dataset = (row.pop("dataset", "games") or "games").strip()
            if dataset not in grouped:
                raise SnapshotError(f"Unsupported CSV dataset '{dataset}' in {path}")
            grouped[dataset].append(row)
        return grouped
    raise SnapshotError(f"Unsupported import format '{fmt}'. Use json or csv.")


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, message: str) -> None:
        self.errors.append(message)


def validate_snapshot(root: Path, league: str, season: str, weeks: list[int] | None = None) -> ValidationReport:
    report = ValidationReport()
    season_dir = Path(root) / league.lower() / str(season)
    if weeks is None:
        weeks = sorted(int(p.name.split("_", 1)[1]) for p in season_dir.glob("week_*") if p.is_dir() and p.name.split("_", 1)[1].isdigit()) if season_dir.exists() else []
    if not weeks:
        report.add_error(f"No snapshot weeks found for {league.upper()} {season}: {season_dir}")
        return report
    for week in weeks:
        wdir = snapshot_week_dir(root, league, season, week)
        if not wdir.exists():
            report.add_error(f"Missing week snapshot for {league.upper()} {season} Week {week}: {wdir}")
            continue
        loaded: dict[str, list[dict[str, Any]]] = {}
        for dataset in DATASETS:
            path = wdir / f"{dataset}.json"
            if not path.exists():
                if dataset in REQUIRED_DATASETS:
                    report.add_error(f"Missing {dataset} file for {league.upper()} {season} Week {week}: {path}")
                continue
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError as exc:
                report.add_error(f"Malformed {dataset} JSON for {league.upper()} {season} Week {week}: {path} ({exc})")
                continue
            if not isinstance(data, list):
                report.add_error(f"Malformed {dataset} records for {league.upper()} {season} Week {week}: expected a list in {path}")
                continue
            loaded[dataset] = data
            report.counts[f"week_{week}.{dataset}"] = len(data)
            missing = [f for f in SCHEMAS[dataset] if any(f not in r for r in data)]
            if missing:
                report.add_error(f"Malformed {dataset} records for {league.upper()} {season} Week {week}: missing fields {sorted(set(missing))}")
        game_ids = {g.get("game_id") for g in loaded.get("games", [])}
        outcome_ids = {o.get("game_id") for o in loaded.get("outcomes", [])}
        for gid in sorted(game_ids - outcome_ids):
            report.add_error(f"Game without matching outcome for {league.upper()} {season} Week {week}: {gid}")
        for odd in loaded.get("odds", []):
            if odd.get("game_id") not in game_ids:
                report.add_error(f"Odds without matching game for {league.upper()} {season} Week {week}: {odd.get('game_id')}")
            if odd.get("market") not in SUPPORTED_MARKETS:
                report.add_error(f"Unsupported market for {league.upper()} {season} Week {week}: {odd.get('market')}")
    return report
