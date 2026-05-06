from datetime import datetime
from nba_api.stats.static import teams
from nba_api.stats.endpoints import leaguegamefinder


def _abbr_to_id_map():
    return {t["abbreviation"]: t["id"] for t in teams.get_teams()}


def get_next_game_context(team_abbreviation, season="2025-26", timeout=20):
    team_abbreviation = team_abbreviation.upper().strip()
    team_map = _abbr_to_id_map()
    if team_abbreviation not in team_map:
        return {
            "opponent": None,
            "home": False,
            "playoff_game": False,
            "game_date": None,
            "source": "Invalid team; used general estimate.",
        }

    try:
        df = leaguegamefinder.LeagueGameFinder(
            team_id_nullable=team_map[team_abbreviation],
            season_nullable=season,
            timeout=timeout,
        ).get_data_frames()[0]

        if df.empty:
            raise ValueError("No schedule rows")

        df["GAME_DATE_DT"] = df["GAME_DATE"].apply(lambda d: datetime.strptime(d, "%Y-%m-%d"))
        upcoming = df[df["GAME_DATE_DT"] >= datetime.utcnow()].sort_values("GAME_DATE_DT")

        if upcoming.empty:
            raise ValueError("No upcoming game found")

        game = upcoming.iloc[0]
        matchup = game["MATCHUP"]
        opponent = matchup.split()[-1]
        home = "vs." in matchup

        return {
            "opponent": opponent,
            "home": home,
            "playoff_game": False,
            "game_date": game["GAME_DATE"],
            "source": "LeagueGameFinder schedule",
        }
    except Exception:
        return {
            "opponent": None,
            "home": False,
            "playoff_game": False,
            "game_date": None,
            "source": "Schedule lookup failed; used general estimate.",
        }
