"""Sport-agnostic discovery and auditable snapshot coverage reporting."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from .nfl_v1_v2_validation import validate_game

DATASETS = ("games", "outcomes", "odds", "team_stats", "injuries", "weather", "player_stats")


def _rows(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def coverage(root: Path, sport: str = "nfl", requested_seasons: list[int] | None = None) -> dict[str, Any]:
    """Return deterministic coverage; a game is valid only when all core checks pass."""
    base = Path(root) / sport.lower()
    periods: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    totals = Counter({name: 0 for name in DATASETS})
    seasons = sorted(p for p in base.iterdir() if p.is_dir()) if base.exists() else []
    for season in seasons:
        for week_dir in sorted(season.glob("week_*")):
            try:
                week = int(week_dir.name.removeprefix("week_"))
            except ValueError:
                continue
            loaded = {name: _rows(week_dir / f"{name}.json") for name in DATASETS}
            counts = {name: len(rows) for name, rows in loaded.items()}
            totals.update(counts)
            valid = 0
            games_with_odds = {r.get("game_id") for r in loaded["odds"]} & {g.get("game_id") for g in loaded["games"]}
            for game in loaded["games"]:
                reasons = validate_game(game, loaded["odds"], loaded["outcomes"], loaded["team_stats"])
                if reasons:
                    exclusions.append({"season": int(season.name), "week": week,
                                       "game_id": game.get("game_id"), "reason_codes": reasons})
                else:
                    valid += 1
            periods.append({"season": int(season.name), "week": week, "valid_games": valid,
                "validation_status": "valid" if loaded["games"] and valid == len(loaded["games"]) else "invalid",
                "games_with_odds": len(games_with_odds),
                "games_without_odds": len(loaded["games"]) - len(games_with_odds), **counts})
    reason_counts = Counter(code for row in exclusions for code in row["reason_codes"])
    requested = sorted(set(requested_seasons or [int(p.name) for p in seasons]))
    available = sorted({row["season"] for row in periods})
    games = totals["games"]
    valid_games = sum(row["valid_games"] for row in periods)
    return {
        "schema_version": 1, "sport": sport.lower(), "requested_seasons": requested,
        "available_seasons": available, "missing_seasons": sorted(set(requested) - set(available)),
        "weeks_discovered": len(periods), "games_discovered": games,
        "games_successfully_snapshotted": valid_games, "invalid_games": games - valid_games,
        "dataset_records": dict(sorted(totals.items())),
        "coverage": {name: {"records": totals[name],
            "percent_of_games": round(100 * totals[name] / games, 2) if games else 0.0}
            for name in DATASETS if name != "games"},
        "exclusion_reason_counts": dict(sorted(reason_counts.items())), "excluded_games": exclusions,
        "periods": periods,
        "source_limitations": [
            "ESPN supplies schedules, final outcomes, and completed prior-game history but not auditable historical market quotes.",
            "The Odds API historical endpoint requires a key and an eligible paid plan; current odds are never substituted.",
            "No timestamp-verifiable injury, pre-kickoff forecast, or player-history archive is bundled.",
        ],
        "timestamp_limitations": "Only records with parseable timestamps strictly before kickoff are eligible inputs.",
    }


def markdown(report: dict[str, Any]) -> str:
    periods = report["periods"]
    lines = ["# NFL historical snapshot coverage", "",
        "> This report describes data availability, not model performance. Synthetic test fixtures are excluded.", "",
        f"- Requested seasons: {', '.join(map(str, report['requested_seasons'])) or 'none'}",
        f"- Available seasons: {', '.join(map(str, report['available_seasons'])) or 'none'}",
        f"- Missing seasons: {', '.join(map(str, report['missing_seasons'])) or 'none'}",
        f"- Weeks discovered: {report['weeks_discovered']}", f"- Games discovered: {report['games_discovered']}",
        f"- Games successfully snapshotted: {report['games_successfully_snapshotted']}",
        f"- Invalid/excluded games: {report['invalid_games']}", "", "## Dataset coverage", "",
        "| Dataset | Records | Records / games |", "|---|---:|---:|"]
    for name, values in report["coverage"].items():
        lines.append(f"| {name} | {values['records']} | {values['percent_of_games']:.2f}% |")
    lines += ["", "## Available weeks", "", "| Season | Week | Games | Valid |", "|---:|---:|---:|---:|"]
    lines += [f"| {p['season']} | {p['week']} | {p['games']} | {p['valid_games']} |" for p in periods]
    if not periods: lines.append("| — | — | 0 | 0 |")
    lines += ["", "## Exclusions", ""]
    lines += [f"- `{code}`: {count}" for code, count in report["exclusion_reason_counts"].items()] or ["- None (no games were discovered)."]
    lines += ["", "## Source and timestamp limitations", ""]
    lines += [f"- {item}" for item in report["source_limitations"]]
    lines += [f"- {report['timestamp_limitations']}", "", "## Reproduction", "", "```bash",
        "python -m backtesting.build_nfl_historical_snapshots --season 2022 --season 2023 --season 2024 --season 2025",
        "python -m backtesting.validate_snapshots --sport nfl", "python -m backtesting.nfl_v1_v2_validation", "```", ""]
    return "\n".join(lines)


def write_coverage(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True); markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    markdown_path.write_text(markdown(report))
