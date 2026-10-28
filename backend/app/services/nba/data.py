"""NBA Stats player-log adapter. This module is not used by NFL."""

from __future__ import annotations

from datetime import datetime, timezone
import logging

import pandas as pd
from nba_api.stats.endpoints import playergamelog
from nba_api.stats.static import players

from .season import active_nba_season
logger = logging.getLogger(__name__)
STAT_COLUMNS = ["PTS", "REB", "AST", "STL", "BLK", "MIN"]
PLAYER_NAME_ALIASES = {
    "karl-anthony towns": ["Karl Anthony Towns"], "karl anthony towns": ["Karl-Anthony Towns"],
    "de'aaron fox": ["DeAaron Fox"], "deaaron fox": ["De'Aaron Fox"],
    "pacôme dadiet": ["Pacome Dadiet"], "pacome dadiet": ["Pacôme Dadiet"],
    "og anunoby": ["O.G. Anunoby"], "o.g. anunoby": ["OG Anunoby"],
}


def _player_lookup_candidates(player_name):
    clean = str(player_name or "").strip()
    return list(dict.fromkeys([clean, *PLAYER_NAME_ALIASES.get(clean.lower(), [])]))


def find_player(player_name):
    searched = []
    for candidate in _player_lookup_candidates(player_name):
        if not candidate:
            continue
        searched.append(candidate)
        matches = players.find_players_by_full_name(candidate)
        if matches:
            exact = [row for row in matches if row["full_name"].lower() == candidate.lower()]
            return exact[0] if exact else matches[0]
    raise ValueError(f"No player found for: {player_name} (tried: {', '.join(searched) or player_name})")


def safe_player_log(player_id, season, season_type, timeout=30):
    retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    error_code = None
    try:
        frame = playergamelog.PlayerGameLog(
            player_id=player_id, season=season, season_type_all_star=season_type, timeout=timeout,
        ).get_data_frames()[0]
    except Exception as exc:
        error_code = type(exc).__name__
        logger.warning("nba_player_log_failed player_id=%s season=%s season_type=%s error=%s",
                       player_id, season, season_type, error_code)
        frame = pd.DataFrame()
    if not frame.empty:
        frame["SEASON_TYPE"] = season_type
    observed_at = None
    if not frame.empty and "GAME_DATE" in frame:
        parsed = pd.to_datetime(frame["GAME_DATE"], errors="coerce", utc=True).dropna()
        if not parsed.empty:
            observed_at = parsed.max().isoformat().replace("+00:00", "Z")
    frame.attrs["source_health"] = {
        "provider": "nba_stats", "status": "ERROR" if error_code else "FRESH" if not frame.empty else "MISSING",
        "retrievedAt": retrieved_at, "dataObservedAt": observed_at,
        "rowCount": int(len(frame)), "errorCode": error_code,
        "missingMeansAvailable": False,
    }
    return frame


def player_log_health(frame):
    """Return explicit provider health without changing the legacy DataFrame API."""
    return dict(getattr(frame, "attrs", {}).get("source_health") or {
        "provider": "nba_stats", "status": "MISSING", "retrievedAt": None,
        "dataObservedAt": None, "rowCount": int(len(frame)) if frame is not None else 0,
        "errorCode": "MISSING_SOURCE_METADATA", "missingMeansAvailable": False,
    })


def get_player_logs(player_name, season=None):
    season = active_nba_season(season).code
    player = find_player(player_name)
    regular = safe_player_log(player["id"], season, "Regular Season")
    playoffs = safe_player_log(player["id"], season, "Playoffs")
    if regular.empty:
        health = player_log_health(regular)
        raise ValueError(
            f"No verified regular season data found for {player['full_name']} in {season} "
            f"(provider_status={health['status']}, error_code={health.get('errorCode') or 'none'})"
        )
    return player, regular, playoffs


def season_summary(frame):
    if frame is None or frame.empty:
        return {"games": 0, **{stat: 0.0 for stat in STAT_COLUMNS}}
    return {"games": int(len(frame)), **{stat: round(float(frame[stat].mean()), 1) for stat in STAT_COLUMNS}}


def combine_logs(regular, playoffs):
    return pd.concat([regular, playoffs], ignore_index=True) if playoffs is not None and not playoffs.empty else regular.copy()


def opponent_specific_summary(logs, opponent):
    if logs is None or logs.empty or not opponent:
        return {"games": 0, **{stat: 0.0 for stat in STAT_COLUMNS}}
    return season_summary(logs[logs["MATCHUP"].str.contains(str(opponent).upper().strip(), na=False)])
