"""Shared normalization for provider and canonical player identifiers."""
from __future__ import annotations

from typing import Any


_MISSING_PLAYER_IDS = {"", "none", "null"}


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
