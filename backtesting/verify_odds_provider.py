"""Verify The Odds API historical odds capabilities without exposing credentials."""
from __future__ import annotations

import json, os
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.error import HTTPError

from nfl_providers import ODDS_API_BASE, NFL_SPORT_KEY, TEAM_MARKETS, _fetch_json, _read_http_error, _redact_url


def _status(value):
    return "yes" if value else "no"


def main() -> int:
    key = os.getenv("THE_ODDS_API_KEY") or os.getenv("ODDS_API_KEY")
    print("Provider: The Odds API")
    print("Subscription tier: unknown (The Odds API does not expose tier name in odds responses)")
    if not key:
        print("Historical support: unverified (THE_ODDS_API_KEY/ODDS_API_KEY is not set)")
        print(f"Markets available: {', '.join(TEAM_MARKETS)} requested on historical /odds; player props require event odds endpoints")
        print("Oldest available date: unverified")
        print("Rate limits: unverified")
        print("Example successful request: unavailable without API key")
        return 2
    params = {"apiKey": key, "regions": "us", "markets": ",".join(TEAM_MARKETS), "oddsFormat": "american", "date": "2025-09-07T12:00:00Z"}
    url = f"{ODDS_API_BASE}/historical/sports/{NFL_SPORT_KEY}/odds?{urlencode(params)}"
    try:
        data = _fetch_json(url)
    except HTTPError as e:
        print(f"Historical support: no ({e.code}: {_read_http_error(e)})")
        print(f"Markets available: requested {', '.join(TEAM_MARKETS)}")
        print("Oldest available date: unavailable")
        print("Rate limits: check x-requests-remaining/x-requests-used headers in provider dashboard")
        print(f"Example successful request: failed {_redact_url(url)}")
        return 1
    events = data.get("data", data) if isinstance(data, dict) else data
    print(f"Historical support: {_status(isinstance(events, list))}")
    markets = sorted({m.get("key") for ev in events or [] for b in ev.get("bookmakers", []) for m in b.get("markets", []) if m.get("key")})
    print(f"Markets available: {', '.join(markets or TEAM_MARKETS)}")
    print("Oldest available date: account-dependent; verified date 2025-09-07T12:00:00Z")
    print("Rate limits: account-dependent; inspect The Odds API response headers/dashboard")
    print(f"Example successful request: {_redact_url(url)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
