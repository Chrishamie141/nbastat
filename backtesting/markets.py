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


def normalize_market(value: Any) -> str:
    """Return the canonical internal market string for provider or snapshot input."""
    if value is None:
        return ""
    text = str(value).strip()
    return ODDS_API_MARKET_ALIASES.get(text.lower(), text).value if text.lower() in ODDS_API_MARKET_ALIASES else text
