import pandas as pd
from nba_api.stats.static import players
from nba_api.stats.endpoints import playergamelog

SEASON = "2025-26"
STAT_COLUMNS = ["PTS", "REB", "AST", "STL", "BLK", "MIN"]


PLAYER_NAME_ALIASES = {
    "karl-anthony towns": ["Karl Anthony Towns"],
    "karl anthony towns": ["Karl-Anthony Towns"],
    "de'aaron fox": ["DeAaron Fox"],
    "deaaron fox": ["De'Aaron Fox"],
    "pacôme dadiet": ["Pacome Dadiet"],
    "pacome dadiet": ["Pacôme Dadiet"],
    "og anunoby": ["O.G. Anunoby"],
    "o.g. anunoby": ["OG Anunoby"],
}


def _player_lookup_candidates(player_name):
    clean_name = str(player_name or "").strip()
    candidates = [clean_name]
    for alias in PLAYER_NAME_ALIASES.get(clean_name.lower(), []):
        if alias not in candidates:
            candidates.append(alias)
    return candidates


def find_player(player_name):
    searched = []
    for candidate in _player_lookup_candidates(player_name):
        if not candidate:
            continue
        searched.append(candidate)
        matches = players.find_players_by_full_name(candidate)
        if matches:
            exact = [p for p in matches if p["full_name"].lower() == candidate.lower()]
            return exact[0] if exact else matches[0]
    searched_names = ", ".join(searched) or str(player_name)
    raise ValueError(f"No player found for: {player_name} (tried: {searched_names})")


def safe_player_log(player_id, season, season_type, timeout=30):
    try:
        df = playergamelog.PlayerGameLog(
            player_id=player_id,
            season=season,
            season_type_all_star=season_type,
            timeout=timeout,
        ).get_data_frames()[0]
    except Exception:
        df = pd.DataFrame()

    if not df.empty:
        df["SEASON_TYPE"] = season_type
    return df


def get_player_logs(player_name, season=SEASON):
    player = find_player(player_name)
    pid = player["id"]
    regular_df = safe_player_log(pid, season, "Regular Season")
    playoff_df = safe_player_log(pid, season, "Playoffs")

    if regular_df.empty:
        raise ValueError(f"No regular season data found for {player['full_name']} in {season}")
    return player, regular_df, playoff_df


def season_summary(df):
    if df is None or df.empty:
        return {"games": 0, **{stat: 0.0 for stat in STAT_COLUMNS}}
    summary = {"games": int(len(df))}
    for stat in STAT_COLUMNS:
        summary[stat] = round(float(df[stat].mean()), 1)
    return summary


def combine_logs(regular_df, playoff_df):
    if playoff_df is not None and not playoff_df.empty:
        return pd.concat([regular_df, playoff_df], ignore_index=True)
    return regular_df.copy()


def opponent_specific_summary(all_logs_df, opponent):
    if all_logs_df is None or all_logs_df.empty or not opponent:
        return {"games": 0, **{stat: 0.0 for stat in STAT_COLUMNS}}

    opp = opponent.upper().strip()
    vs_df = all_logs_df[all_logs_df["MATCHUP"].str.contains(opp, na=False)]
    return season_summary(vs_df)
