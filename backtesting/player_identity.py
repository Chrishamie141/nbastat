"""Shared normalization for provider and canonical player identifiers."""
from __future__ import annotations

from typing import Any


_MISSING_PLAYER_IDS = {"", "none", "null", "unknown"}


def normalize_player_id(value: Any) -> str | None:
    """Return a trimmed string ID, or ``None`` for every null spelling.

    Provider exports pass through JSON, CSV, and pandas-shaped code paths, so
    Python ``None`` is sometimes serialized as text.  Treating those spellings
    centrally prevents an unresolved identity from becoming a canonical one.
    """
    if value is None:
        return None
    normalized = str(value).strip()
    return None if normalized.casefold() in _MISSING_PLAYER_IDS else normalized


def first_player_id(*values: Any) -> str | None:
    """Return the first real player ID from a provider's alias fields."""
    for value in values:
        normalized = normalize_player_id(value)
        if normalized is not None:
            return normalized
    return None


def canonical_player_key(game_id: Any, player_id: Any) -> tuple[str, str] | None:
    """Return the shared, exact game/player identity used by keyed joins."""
    game = str(game_id or "").strip()
    player = normalize_player_id(player_id)
    return (game, player) if game and player is not None else None
