"""Snapshot path, schema, import, and validation helpers for internal backtesting."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
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
            row.setdefault("record_role", record.get("record_role", "pregame_history"))
        if name != "injuries":
            row.setdefault("game_id", record.get("game_id"))
        row.setdefault("source", record.get("source", "unknown"))
        row.setdefault("captured_at", record.get("captured_at") or record.get("data_as_of") or record.get("kickoff_time"))
        row.setdefault("data_as_of", record.get("data_as_of") or row.get("captured_at"))
        row.setdefault("is_pregame", bool(record.get("is_pregame", name not in {"outcomes"} and row.get("record_role") != "game_outcome")))
        row.setdefault("season", str(season))
        row.setdefault("week", int(week))
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

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


def _parse_iso(value: str) -> datetime | None:
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None



def _require_meta(report: ValidationReport, dataset: str, row: dict[str, Any], league: str, season: str, week: int) -> None:
    for field in ("source", "captured_at", "data_as_of", "is_pregame", "season", "week"):
        if field not in row:
            report.add_error(f"missing_data_as_of: {dataset} record missing {field} for {league.upper()} {season} Week {week}")
    if dataset not in {"injuries", "player_stats", "team_stats"} and "game_id" not in row:
        report.add_error(f"missing_data_as_of: {dataset} record missing game_id for {league.upper()} {season} Week {week}")


def _record_key(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, default=str)


def validate_snapshot(root: Path, league: str, season: str, weeks: list[int] | None = None, *, strict: bool = False, require_backtest_ready: bool = False) -> ValidationReport:
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
                if dataset in REQUIRED_DATASETS or strict or require_backtest_ready:
                    report.add_error(f"Missing {dataset} file for {league.upper()} {season} Week {week}: {path}")
                else:
                    report.add_warning(f"Optional dataset {dataset} missing for {league.upper()} {season} Week {week}: {path}")
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
            if not data and (dataset in REQUIRED_DATASETS or strict or require_backtest_ready):
                report.add_error(f"Empty required dataset {dataset} for {league.upper()} {season} Week {week}")
            report.counts[f"week_{week}.{dataset}"] = len(data)
            missing = [f for f in SCHEMAS[dataset] if any(f not in r for r in data)]
            if missing:
                report.add_error(f"Malformed {dataset} records for {league.upper()} {season} Week {week}: missing fields {sorted(set(missing))}")
            seen = set()
            for r in data:
                k = _record_key(r)
                if k in seen:
                    report.add_error(f"duplicate_record: duplicate {dataset} record for {league.upper()} {season} Week {week}")
                seen.add(k)
                _require_meta(report, dataset, r, league, season, week)
        games = loaded.get("games", [])
        game_ids = {g.get("game_id") for g in games}
        if len(game_ids) != len(games):
            report.add_error(f"Duplicate game IDs for {league.upper()} {season} Week {week}")
        kickoff_by_game = {g.get("game_id"): g.get("kickoff_time") for g in games}
        for game in games:
            if str(game.get("league", "")).lower() != league.lower():
                report.add_error(f"Game from wrong league for {league.upper()} {season} Week {week}: {game.get('game_id')}")
            if str(game.get("season")) != str(season):
                report.add_error(f"Game from wrong season for {league.upper()} {season} Week {week}: {game.get('game_id')}")
            if int(game.get("week", -1)) != int(week):
                report.add_error(f"Game from wrong week for {league.upper()} {season} Week {week}: {game.get('game_id')}")
            if not _parse_iso(game.get("kickoff_time")):
                report.add_error(f"invalid_kickoff_time: {game.get('game_id')} for {league.upper()} {season} Week {week}")
        outcome_ids = {o.get("game_id") for o in loaded.get("outcomes", [])}
        for gid in sorted(game_ids - outcome_ids):
            report.add_error(f"Game without matching outcome for {league.upper()} {season} Week {week}: {gid}")
        for gid in sorted(outcome_ids - game_ids):
            report.add_error(f"Outcome without matching game for {league.upper()} {season} Week {week}: {gid}")
        for outcome in loaded.get("outcomes", []):
            if outcome.get("completed_at") and not _parse_iso(outcome.get("completed_at")):
                report.add_error(f"invalid_completed_at: outcome for {outcome.get('game_id')}")
        for odd in loaded.get("odds", []):
            if odd.get("game_id") not in game_ids:
                report.add_error(f"Odds without matching game for {league.upper()} {season} Week {week}: {odd.get('game_id')}")
            if odd.get("market") not in SUPPORTED_MARKETS:
                report.add_error(f"Unsupported market for {league.upper()} {season} Week {week}: {odd.get('market')}")
            for field in ("sportsbook", "market", "line", "odds"):
                if odd.get(field) is None:
                    report.add_error(f"missing_odds_field: odds missing {field} for {league.upper()} {season} Week {week}: {odd.get('game_id')}")
            if str(odd.get("source", "")).lower() in {"current", "live", "the-odds-api-current"}:
                report.add_error(f"current_data_labeled_historical: odds for {odd.get('game_id')}")
        for dataset in ("odds", "weather"):
            for row in loaded.get(dataset, []):
                captured_at = row.get("captured_at")
                kickoff = kickoff_by_game.get(row.get("game_id"))
                if captured_at and kickoff and _parse_iso(captured_at) and _parse_iso(kickoff) and _parse_iso(captured_at) > _parse_iso(kickoff):
                    code = "odds_after_kickoff" if dataset == "odds" else "weather_after_kickoff"
                    report.add_error(f"{code}: Future-data leakage in {dataset} for {league.upper()} {season} Week {week}: {row.get('game_id')} captured after kickoff")
                if dataset == "weather" and str(row.get("source", "")).lower() in {"openweather", "current", "live"}:
                    report.add_error(f"weather_not_historical: current/live weather cannot be used for historical game {row.get('game_id')}")
        for row in loaded.get("injuries", []):
            gid = row.get("game_id")
            if gid and gid in kickoff_by_game and _parse_iso(row.get("captured_at")) and _parse_iso(kickoff_by_game[gid]) and _parse_iso(row.get("captured_at")) > _parse_iso(kickoff_by_game[gid]):
                report.add_error(f"injury_report_after_kickoff: {gid}")
        for dataset in ("player_stats", "team_stats"):
            for row in loaded.get(dataset, []):
                role = row.get("record_role", "pregame_history")
                if role == "pregame_history" and (str(row.get("season")) != str(season) or int(row.get("through_week", -1)) >= int(week)):
                    report.add_error(f"same_game_stats_in_pregame_history: Future-data leakage in {dataset} for {league.upper()} {season} Week {week}: through_week must be before replay week")
        manifest = wdir / "manifest.json"
        if manifest.exists():
            try:
                meta = json.loads(manifest.read_text())
                for d, info in (meta.get("datasets") or {}).items():
                    key = f"week_{week}.{d}"
                    if key in report.counts and int(info.get("records", -1)) != report.counts[key]:
                        report.add_error(f"manifest_count_mismatch: {d} manifest count does not match file for {league.upper()} {season} Week {week}")
            except json.JSONDecodeError as exc:
                report.add_error(f"Malformed manifest JSON for {league.upper()} {season} Week {week}: {manifest} ({exc})")
    return report
