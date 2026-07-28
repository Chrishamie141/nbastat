"""Canonical game outcome identities shared by replay, grading, and validation."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .grader import canonical_team


ID_FIELDS = ("game_id", "game", "id", "event_id", "provider_event_id", "source_event_id", "raw_event_id")
GAME_ALIAS_FIELDS = ID_FIELDS + ("provider_game_id", "espn_event_id")


def game_id(row: dict[str, Any]) -> str | None:
    """Return the primary identifier carried by a record (not necessarily canonical)."""
    value = next((row.get(field) for field in ID_FIELDS if row.get(field) not in (None, "")), None)
    return str(value) if value not in (None, "") else None


def canonical_game_key(league: str, season: str | int, week: int, value: Any) -> tuple[str, str, int, str]:
    """Build the canonical multi-week game identity."""
    return (str(league).lower(), str(season), int(week), str(value))


def _id_variants(value: Any) -> set[str]:
    """Return equivalent representations used by known snapshot providers."""
    if value in (None, ""):
        return set()
    text = str(value).strip()
    variants = {text}
    # ESPN schedule snapshots use ``espn-<event id>`` while scoreboard finals
    # expose the numeric event id in ``provider_event_id``.
    if text.casefold().startswith("espn-"):
        variants.add(text.split("-", 1)[1])
    return variants


def _team(row: dict[str, Any], side: str) -> str:
    value = row.get(f"{side}_team") or row.get(side)
    if isinstance(value, dict):
        value = value.get("abbreviation") or value.get("name") or value.get("display_name")
    return canonical_team(value)


def _score(row: dict[str, Any], side: str) -> Any:
    direct = row.get(f"final_{side}_score")
    if direct is not None:
        return direct
    scores = row.get("scores")
    if isinstance(scores, dict):
        value = scores.get(side)
        if isinstance(value, dict):
            value = value.get("score") or value.get("value")
        return value
    value = row.get(f"{side}_score")
    return value.get("score") if isinstance(value, dict) else value


def _timestamp(value: Any) -> float:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return float("-inf")


def normalize_outcomes(
    outcomes: list[dict[str, Any]], games: list[dict[str, Any]], league: str, season: str, week: int
) -> list[dict[str, Any]]:
    """Reconcile provider finals to the authoritative same-week game universe.

    Matching is ordered: exact canonical ID, known provider/source alias, then a
    unique home/away matchup. Provider identifiers remain available for audit,
    but can never overwrite the canonical ``game_id`` after a match.
    Duplicate provider rows are reduced deterministically to the latest complete
    final for each canonical game.
    """
    canonical_by_id = {str(game_id(game)): game for game in games if game_id(game) is not None}
    aliases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    matchups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for game in games:
        for field in GAME_ALIAS_FIELDS:
            for alias in _id_variants(game.get(field)):
                if game not in aliases[alias]:
                    aliases[alias].append(game)
        matchups[(_team(game, "home"), _team(game, "away"))].append(game)

    reconciled: list[dict[str, Any]] = []
    for raw in outcomes:
        raw_ids = [(field, str(raw[field])) for field in ID_FIELDS if raw.get(field) not in (None, "")]
        primary_id = raw_ids[0][1] if raw_ids else None
        game = canonical_by_id.get(primary_id or "")
        method = "exact_game_id" if game is not None else None
        if game is None:
            candidates: list[dict[str, Any]] = []
            for _, value in raw_ids:
                for variant in _id_variants(value):
                    for candidate in aliases.get(variant, []):
                        if candidate not in candidates:
                            candidates.append(candidate)
            if len(candidates) == 1:
                game, method = candidates[0], "provider_id_alias"
        if game is None:
            candidates = matchups.get((_team(raw, "home"), _team(raw, "away")), [])
            if len(candidates) == 1 and all((_team(raw, side) for side in ("home", "away"))):
                game, method = candidates[0], "home_away"

        canonical_id = game_id(game or {}) or primary_id
        row = {**(game or {}), **raw}
        source_game_id = raw.get("source_game_id") or primary_id
        row.update({
            "league": str((game or {}).get("league") or raw.get("league") or league).lower(),
            "season": str((game or {}).get("season") or raw.get("season") or season),
            "week": int((game or {}).get("week") or raw.get("week") or week),
            "game_id": canonical_id,
            "home_team": (game or {}).get("home_team") or raw.get("home_team") or raw.get("home"),
            "away_team": (game or {}).get("away_team") or raw.get("away_team") or raw.get("away"),
            "final_home_score": _score(raw, "home"),
            "final_away_score": _score(raw, "away"),
            "completed_at": raw.get("completed_at") or raw.get("date") or raw.get("last_updated"),
            "source": raw.get("source") or "unknown",
            "match_method": method or "unmatched",
            "match_success": game is not None,
        })
        has_scores = row["final_home_score"] is not None and row["final_away_score"] is not None
        final_status = str(raw.get("status") or "").lower() in {"status_final", "final", "post"}
        row["completed"] = bool(has_scores and (raw.get("completed") is True or final_status or "completed" not in raw))
        if source_game_id:
            row["source_game_id"] = source_game_id
        for field in ("provider_event_id", "source_event_id", "raw_event_id"):
            if raw.get(field) not in (None, ""):
                row[field] = raw[field]
        if game is not None:
            row["game"] = canonical_id
        reconciled.append(row)

    # Prefer complete finals, then completed rows, then the newest provider row.
    selected: dict[str, dict[str, Any]] = {}
    unmatched: list[dict[str, Any]] = []
    for row in reconciled:
        if not row["match_success"] or row.get("game_id") is None:
            unmatched.append(row)
            continue
        key = str(row["game_id"])
        rank = (row.get("final_home_score") is not None and row.get("final_away_score") is not None,
                bool(row.get("completed")), _timestamp(row.get("completed_at")), str(row.get("source_game_id") or ""))
        current = selected.get(key)
        if current is None or rank > current["_reconciliation_rank"]:
            row["_reconciliation_rank"] = rank
            selected[key] = row
    result = list(selected.values()) + unmatched
    for row in result:
        row.pop("_reconciliation_rank", None)
    return result
