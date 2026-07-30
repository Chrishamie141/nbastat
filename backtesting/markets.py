"""Canonical betting market normalization shared by providers, snapshots, validators, and replay."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class Market(StrEnum):
    H2H = "h2h"
    SPREAD = "spread"
    TOTAL = "total"
    PLAYER_PROP = "player_prop"
    PASS_YDS = "PASS_YDS"
    RUSH_YDS = "RUSH_YDS"
    REC_YDS = "REC_YDS"
    RECEPTIONS = "RECEPTIONS"
    TD = "TD"
    PASS_TD = "PASS_TD"
    PASS_INT = "PASS_INT"


ODDS_API_MARKET_ALIASES = {
    "h2h": Market.H2H,
    "moneyline": Market.H2H,
    "moneylines": Market.H2H,
    "spreads": Market.SPREAD,
    "spread": Market.SPREAD,
    "totals": Market.TOTAL,
    "total": Market.TOTAL,
    "over_under": Market.TOTAL,
    "player_pass_yds": Market.PASS_YDS,
    "player_rush_yds": Market.RUSH_YDS,
    "player_reception_yds": Market.REC_YDS,
    "player_receptions": Market.RECEPTIONS,
    "player_anytime_td": Market.TD,
    "player_pass_tds": Market.PASS_TD,
    "player_pass_interceptions": Market.PASS_INT,
    "player_prop": Market.PLAYER_PROP,
}

# Backward-compatible accepted values include legacy snapshot markets.
SUPPORTED_MARKETS = {market.value for market in Market} | {"moneyline"}
CANONICAL_TEAM_MARKETS = (Market.H2H.value, Market.SPREAD.value, Market.TOTAL.value)
ODDS_API_TEAM_MARKETS = ("h2h", "spreads", "totals")

# The only player markets approved for the first pricing evaluation.  Provider
# keys belong here (and nowhere in simulation/grading code).
PLAYER_PROP_MARKET_ALIASES = {
    "player_pass_yds": "passing_yards",
    "player_pass_tds": "passing_tds",
    "player_rush_attempts": "rushing_attempts",
    "player_rush_yds": "rushing_yards",
    "player_receptions": "receptions",
    "player_reception_yds": "receiving_yards",
    "passing_yards": "passing_yards",
    "passing_tds": "passing_tds",
    "rushing_attempts": "rushing_attempts",
    "rushing_yards": "rushing_yards",
    "receptions": "receptions",
    "receiving_yards": "receiving_yards",
}
CANONICAL_PLAYER_PROP_MARKETS = tuple(dict.fromkeys(PLAYER_PROP_MARKET_ALIASES.values()))
ODDS_API_PLAYER_PROP_MARKETS = tuple(
    key for key in PLAYER_PROP_MARKET_ALIASES if key.startswith("player_")
)


def normalize_player_prop_market(value: Any) -> str | None:
    """Return a supported canonical prop market, or ``None`` explicitly."""
    return PLAYER_PROP_MARKET_ALIASES.get(str(value or "").strip().casefold())

# Internal player markets are intentionally upper-case.  Keep this lookup
# separate from provider aliases so values stored by older snapshots (and CLI
# values with arbitrary casing) meet predictor output at the same canonical
# name.
_INTERNAL_MARKETS_BY_CASEFOLD = {market.value.casefold(): market.value for market in Market}


def normalize_market(value: Any) -> str:
    """Return the canonical internal market string for provider or snapshot input."""
    if value is None:
        return ""
    text = str(value).strip()
    folded = text.casefold()
    if folded in ODDS_API_MARKET_ALIASES:
        return ODDS_API_MARKET_ALIASES[folded].value
    return _INTERNAL_MARKETS_BY_CASEFOLD.get(folded, text)


def normalize_markets(values: Any) -> tuple[str, ...]:
    """Normalize and de-duplicate market names without changing their order."""
    normalized: list[str] = []
    for value in values:
        market = normalize_market(value)
        if market and market not in normalized:
            normalized.append(market)
    return tuple(normalized)
