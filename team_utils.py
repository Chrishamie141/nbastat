"""Shared NBA team abbreviation normalization helpers."""

TEAM_ABBREVIATION_ALIASES = {
    "SA": "SAS",
    "NY": "NYK",
    "GS": "GSW",
    "PHO": "PHX",
    "NO": "NOP",
    "UTAH": "UTA",
    "WSH": "WAS",
    # nba_api and the rest of this app use BKN/CHA, so normalize aliases
    # toward those canonical abbreviations consistently.
    "BRK": "BKN",
    "BKN": "BKN",
    "CHO": "CHA",
    "CHA": "CHA",
}


def normalize_team_abbreviation(team_abbr: str) -> str:
    """Return this app's canonical NBA abbreviation for common aliases."""
    normalized = str(team_abbr or "").upper().strip()
    return TEAM_ABBREVIATION_ALIASES.get(normalized, normalized)
