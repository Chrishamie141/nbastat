"""Canonical NBA season handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os
import re


@dataclass(frozen=True)
class NBASeason:
    start_year: int
    end_year: int
    code: str


def normalize_nba_season(value: str | int | NBASeason) -> NBASeason:
    """Return the canonical ``YYYY-YY`` NBA season identity.

    An integer always means the season's starting year.  Four-digit ending
    years are accepted for operational inputs, but the persisted code remains
    the NBA provider format (for example ``2026-27``).
    """
    if isinstance(value, NBASeason):
        return value
    if isinstance(value, int) or str(value).strip().isdigit() and len(str(value).strip()) == 4:
        start = int(value)
        return NBASeason(start, start + 1, f"{start}-{str(start + 1)[-2:]}")
    text = str(value or "").strip()
    match = re.fullmatch(r"(20\d{2})[-/](\d{2}|20\d{2})", text)
    if not match:
        raise ValueError(f"Invalid NBA season {value!r}; expected YYYY-YY or starting year")
    start = int(match.group(1))
    raw_end = match.group(2)
    end = int(raw_end) if len(raw_end) == 4 else (start // 100) * 100 + int(raw_end)
    if end <= start:
        end += 100
    if end != start + 1:
        raise ValueError(f"NBA season must span consecutive years: {value!r}")
    return NBASeason(start, end, f"{start}-{str(end)[-2:]}")


def current_nba_season(today: date | None = None) -> NBASeason:
    today = today or date.today()
    start = today.year if today.month >= 7 else today.year - 1
    return normalize_nba_season(start)


def active_nba_season(configured: str | int | NBASeason | None = None, *, today: date | None = None) -> NBASeason:
    """Resolve an optional deployment override or the season active today."""
    value = configured if configured not in (None, "") else os.getenv("NBA_ACTIVE_SEASON")
    return normalize_nba_season(value) if value else current_nba_season(today)


DEFAULT_NBA_SEASON = current_nba_season().code
