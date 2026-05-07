from datetime import datetime, timedelta
import pandas as pd
from nba_api.stats.static import teams
from nba_api.stats.endpoints import leaguegamefinder, scoreboardv2


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

    def _general_estimate():
        return {
            "opponent": None,
            "home": False,
            "playoff_game": False,
            "game_date": None,
            "source": "Schedule lookup failed; used general estimate.",
        }

    team_id = team_map[team_abbreviation]
    today = datetime.utcnow().date()

    # Primary source: ScoreboardV2 from today through the next 14 days.
    for day_offset in range(0, 15):
        dt = today + timedelta(days=day_offset)
        game_date = dt.strftime("%m/%d/%Y")
        try:
            sb = scoreboardv2.ScoreboardV2(
                game_date=game_date,
                timeout=min(timeout, 8),
            )
            line_score_df = sb.line_score.get_data_frame()
            game_summary_df = sb.game_summary.get_data_frame()
            if line_score_df.empty:
                continue

            team_rows = line_score_df[line_score_df["TEAM_ID"] == team_id]
            if team_rows.empty:
                continue

            game_id = str(team_rows.iloc[0]["GAME_ID"])
            game_rows = line_score_df[line_score_df["GAME_ID"].astype(str) == game_id]
            if len(game_rows) < 2:
                continue

            opp_row = game_rows[game_rows["TEAM_ID"] != team_id].iloc[0]
            home = bool(int(team_rows.iloc[0]["TEAM_ID"]) == int(game_rows.iloc[-1]["TEAM_ID"]))
            playoff_game = False
            if not game_summary_df.empty and "GAME_STATUS_TEXT" in game_summary_df.columns:
                summary_row = game_summary_df[game_summary_df["GAME_ID"].astype(str) == game_id]
                if not summary_row.empty:
                    txt = str(summary_row.iloc[0]["GAME_STATUS_TEXT"]).lower()
                    playoff_game = "playoff" in txt or "postseason" in txt

            return {
                "opponent": str(opp_row["TEAM_ABBREVIATION"]).upper(),
                "home": home,
                "playoff_game": playoff_game,
                "game_date": dt.strftime("%Y-%m-%d"),
                "source": "ScoreboardV2",
            }
        except Exception:
            continue

    # Fallback: LeagueGameFinder schedule-style lookup.
    try:
        df = leaguegamefinder.LeagueGameFinder(
            team_id_nullable=team_id,
            season_nullable=season,
            timeout=min(timeout, 8),
        ).get_data_frames()[0]
        if df.empty:
            return _general_estimate()

        df["GAME_DATE_DT"] = pd.to_datetime(df["GAME_DATE"], errors="coerce")
        df = df.dropna(subset=["GAME_DATE_DT"]).sort_values("GAME_DATE_DT")
        upcoming = df[df["GAME_DATE_DT"].dt.date >= today]
        if upcoming.empty:
            return _general_estimate()

        game = upcoming.iloc[0]
        matchup = str(game["MATCHUP"])
        opponent = matchup.split()[-1].upper() if matchup else None
        return {
            "opponent": opponent,
            "home": "vs." in matchup,
            "playoff_game": False,
            "game_date": game["GAME_DATE_DT"].strftime("%Y-%m-%d"),
            "source": "LeagueGameFinder",
        }
    except Exception:
        return _general_estimate()
