"""Canonical game outcome identities shared by replay, grading, and validation."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .grader import canonical_team


def game_id(row: dict[str, Any]) -> str | None:
    """Return the provider-independent game identifier carried by a record."""
    value = row.get("game_id") or row.get("game") or row.get("id")
    return str(value) if value not in (None, "") else None


def canonical_game_key(league: str, season: str | int, week: int, value: Any) -> tuple[str, str, int, str]:
    """Build the canonical multi-week game identity."""
    return (str(league).lower(), str(season), int(week), str(value))


def normalize_outcomes(
    outcomes: list[dict[str, Any]], games: list[dict[str, Any]], league: str, season: str, week: int
) -> list[dict[str, Any]]:
    """Normalize finals and reconcile provider aliases without team-name grading.

    A provider ID is accepted directly or through an explicit alias.  A unique
    home/away matchup is a final ingestion-time reconciliation fallback; the
    grader still indexes and looks up only the resulting canonical game ID.
    """
    aliases: dict[str, dict[str, Any]] = {}
    matchups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for game in games:
        for field in ("game_id", "game", "id", "event_id", "provider_game_id", "espn_event_id"):
            if game.get(field) not in (None, ""):
                aliases[str(game[field])] = game
        matchups[(canonical_team(game.get("home_team")), canonical_team(game.get("away_team")))].append(game)

    normalized = []
    for raw in outcomes:
        source_id = game_id(raw)
        game = aliases.get(source_id or "")
        if game is None:
            candidates = matchups.get((canonical_team(raw.get("home_team")), canonical_team(raw.get("away_team"))), [])
            if len(candidates) == 1:
                game = candidates[0]
        canonical_id = game_id(game or {}) or source_id
        row = {**(game or {}), **raw}
        row.update({
            "league": str(row.get("league") or league).lower(),
            "season": str(row.get("season") or season),
            "week": int(row.get("week") or week),
            "game_id": canonical_id,
            "home_team": row.get("home_team") or (game or {}).get("home_team"),
            "away_team": row.get("away_team") or (game or {}).get("away_team"),
            "final_home_score": row.get("final_home_score"),
            "final_away_score": row.get("final_away_score"),
            "completed": bool(row.get("completed", row.get("final_home_score") is not None and row.get("final_away_score") is not None)),
            "completed_at": row.get("completed_at"),
            "source": row.get("source") or "unknown",
        })
        if source_id and source_id != canonical_id:
            row["source_game_id"] = source_id
        normalized.append(row)
    return normalized
