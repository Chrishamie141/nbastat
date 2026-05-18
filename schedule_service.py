from datetime import datetime, timedelta
import requests
import pandas as pd
from nba_api.stats.static import teams
from nba_api.stats.endpoints import leaguegamefinder, scoreboardv2


ESPN_TEAM_MAP = {
    "ATL": "Atlanta Hawks",
    "BOS": "Boston Celtics",
    "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets",
    "CHI": "Chicago Bulls",
    "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks",
    "DEN": "Denver Nuggets",
    "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors",
    "HOU": "Houston Rockets",
    "IND": "Indiana Pacers",
    "LAC": "LA Clippers",
    "LAL": "Los Angeles Lakers",
    "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks",
    "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans",
    "NYK": "New York Knicks",
    "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic",
    "PHI": "Philadelphia 76ers",
    "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers",
    "SAC": "Sacramento Kings",
    "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors",
    "UTA": "Utah Jazz",
    "WAS": "Washington Wizards",
}


def _abbr_to_id_map():
    return {team["abbreviation"]: team["id"] for team in teams.get_teams()}


def _id_to_abbr_map():
    return {team["id"]: team["abbreviation"] for team in teams.get_teams()}


def _general_estimate(reason):
    return {
        "opponent": None,
        "home": False,
        "playoff_game": False,
        "game_date": None,
        "source": f"{reason}; used general estimate.",
    }


def _find_with_espn(team_abbreviation, days_ahead=30, timeout=8):
    team_name = ESPN_TEAM_MAP.get(team_abbreviation)

    if not team_name:
        return None

    today = datetime.utcnow().date()

    for offset in range(days_ahead + 1):
        date = today + timedelta(days=offset)
        date_string = date.strftime("%Y%m%d")

        url = (
            "https://site.api.espn.com/apis/site/v2/sports/"
            f"basketball/nba/scoreboard?dates={date_string}"
        )

        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            data = response.json()
        except Exception:
            continue

        events = data.get("events", [])

        for event in events:
            competitions = event.get("competitions", [])

            if not competitions:
                continue

            competition = competitions[0]
            competitors = competition.get("competitors", [])

            if len(competitors) < 2:
                continue

            found_team = None
            opponent_team = None

            for competitor in competitors:
                display_name = competitor.get("team", {}).get("displayName")
                abbreviation = competitor.get("team", {}).get("abbreviation")
                home_away = competitor.get("homeAway")

                if display_name == team_name or abbreviation == team_abbreviation:
                    found_team = competitor
                else:
                    opponent_team = competitor

            if found_team and opponent_team:
                opponent_abbr = opponent_team.get("team", {}).get("abbreviation")
                is_home = found_team.get("homeAway") == "home"

                return {
                    "opponent": opponent_abbr,
                    "home": is_home,
                    "playoff_game": True,
                    "game_date": date.strftime("%Y-%m-%d"),
                    "source": "ESPN Scoreboard",
                }

    return None


def _find_with_nba_scoreboard(team_abbreviation, team_id, days_ahead=30, timeout=8):
    id_map = _id_to_abbr_map()
    today = datetime.utcnow().date()

    for offset in range(days_ahead + 1):
        date = today + timedelta(days=offset)
        game_date = date.strftime("%m/%d/%Y")

        try:
            scoreboard = scoreboardv2.ScoreboardV2(
                game_date=game_date,
                timeout=timeout,
            )

            line_score_df = scoreboard.line_score.get_data_frame()
            game_header_df = scoreboard.game_header.get_data_frame()

            if line_score_df.empty:
                continue

            team_rows = line_score_df[line_score_df["TEAM_ID"] == team_id]

            if team_rows.empty:
                continue

            game_id = str(team_rows.iloc[0]["GAME_ID"])

            game_rows = line_score_df[
                line_score_df["GAME_ID"].astype(str) == game_id
            ]

            if len(game_rows) < 2:
                continue

            opponent_rows = game_rows[game_rows["TEAM_ID"] != team_id]

            if opponent_rows.empty:
                continue

            opponent_row = opponent_rows.iloc[0]
            opponent_id = int(opponent_row["TEAM_ID"])
            opponent = id_map.get(opponent_id)

            home = False

            if not game_header_df.empty:
                header_row = game_header_df[
                    game_header_df["GAME_ID"].astype(str) == game_id
                ]

                if not header_row.empty and "HOME_TEAM_ID" in header_row.columns:
                    home_team_id = int(header_row.iloc[0]["HOME_TEAM_ID"])
                    home = home_team_id == int(team_id)

            return {
                "opponent": opponent,
                "home": home,
                "playoff_game": True,
                "game_date": date.strftime("%Y-%m-%d"),
                "source": "NBA ScoreboardV2",
            }

        except Exception:
            continue

    return None


def _find_with_league_gamefinder(team_id, season, timeout=8):
    try:
        df = leaguegamefinder.LeagueGameFinder(
            team_id_nullable=team_id,
            season_nullable=season,
            timeout=timeout,
        ).get_data_frames()[0]

        if df.empty:
            return None

        df["GAME_DATE_DT"] = pd.to_datetime(df["GAME_DATE"], errors="coerce")
        df = df.dropna(subset=["GAME_DATE_DT"]).sort_values("GAME_DATE_DT")

        today = datetime.utcnow().date()
        upcoming = df[df["GAME_DATE_DT"].dt.date >= today]

        if upcoming.empty:
            return None

        game = upcoming.iloc[0]
        matchup = str(game["MATCHUP"])
        opponent = matchup.split()[-1].upper() if matchup else None

        return {
            "opponent": opponent,
            "home": "vs." in matchup,
            "playoff_game": True,
            "game_date": game["GAME_DATE_DT"].strftime("%Y-%m-%d"),
            "source": "LeagueGameFinder",
        }

    except Exception:
        return None


def get_next_game_context(team_abbreviation, season="2025-26", timeout=8):
    team_abbreviation = team_abbreviation.upper().strip()
    team_map = _abbr_to_id_map()

    if team_abbreviation not in team_map:
        return _general_estimate("Invalid team")

    team_id = team_map[team_abbreviation]

    espn_result = _find_with_espn(
        team_abbreviation=team_abbreviation,
        days_ahead=30,
        timeout=timeout,
    )

    if espn_result:
        return espn_result

    nba_result = _find_with_nba_scoreboard(
        team_abbreviation=team_abbreviation,
        team_id=team_id,
        days_ahead=30,
        timeout=timeout,
    )

    if nba_result:
        return nba_result

    gamefinder_result = _find_with_league_gamefinder(
        team_id=team_id,
        season=season,
        timeout=timeout,
    )

    if gamefinder_result:
        return gamefinder_result

    return _general_estimate("No upcoming playoff game found")