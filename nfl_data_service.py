"""NFL data access placeholders.

These functions intentionally return sample data until real NFL stat, schedule,
and odds providers are connected. Replace internals with calls to an official
or licensed data source before using for production wagering decisions.
"""

NFL_SAMPLE_PLAYERS = [
    {"player": "Patrick Mahomes", "team": "KC", "position": "QB", "opponent": "LAC"},
    {"player": "Christian McCaffrey", "team": "SF", "position": "RB", "opponent": "SEA"},
    {"player": "Justin Jefferson", "team": "MIN", "position": "WR", "opponent": "GB"},
    {"player": "Amon-Ra St. Brown", "team": "DET", "position": "WR", "opponent": "CHI"},
    {"player": "Josh Allen", "team": "BUF", "position": "QB", "opponent": "NYJ"},
]


def get_nfl_player_pool(team=None):
    if not team:
        return list(NFL_SAMPLE_PLAYERS)
    team_key = str(team).strip().upper()
    return [player for player in NFL_SAMPLE_PLAYERS if player["team"] == team_key]


def get_nfl_lines():
    """Return placeholder sportsbook lines keyed by player and stat.

    Future integration point: replace this with odds API output normalized to
    {player: {STAT: [{line, odds}, ...]}}.
    """
    return {
        "Patrick Mahomes": {"PASS_YDS": [{"line": 249.5, "odds": -115}], "PASS_TD": [{"line": 1.5, "odds": -130}]},
        "Christian McCaffrey": {"RUSH_YDS": [{"line": 64.5, "odds": -110}], "TD": [{"line": 0.5, "odds": -125}]},
        "Justin Jefferson": {"REC_YDS": [{"line": 79.5, "odds": -110}], "RECEPTIONS": [{"line": 5.5, "odds": -120}]},
        "Amon-Ra St. Brown": {"REC_YDS": [{"line": 69.5, "odds": -110}], "RECEPTIONS": [{"line": 6.5, "odds": +105}]},
        "Josh Allen": {"PASS_YDS": [{"line": 239.5, "odds": -110}], "RUSH_YDS": [{"line": 35.5, "odds": -115}]},
    }


def get_team_market_placeholders(team):
    team_key = str(team or "NFL").strip().upper()
    return [
        {"team": team_key, "market": "MONEYLINE", "prediction": f"{team_key} moneyline lean", "confidence": 52, "notes": "Placeholder team market; connect real odds."},
        {"team": team_key, "market": "SPREAD", "prediction": f"{team_key} spread lean", "confidence": 51, "notes": "Placeholder team market; connect real odds."},
        {"team": team_key, "market": "TOTAL", "prediction": "Game total lean", "confidence": 50, "notes": "Placeholder total; connect real odds."},
    ]
