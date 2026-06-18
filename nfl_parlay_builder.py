"""NFL parlay builder using live providers first and sample data as fallback."""

from __future__ import annotations

from statistics import mean

from models import DifficultyLevel, Parlay, ParlayLeg, ParlayResult, SportType
from nfl_data_service import (
    get_nfl_games,
    get_nfl_injuries,
    get_nfl_player_props,
    get_nfl_player_recent_stats,
    get_nfl_team_lines,
    get_nfl_weather,
)
from prediction_storage import save_parlay_result

DIFFICULTY_RULES = {
    DifficultyLevel.SAFE: {"legs": 3, "min_confidence": 62},
    DifficultyLevel.BALANCED: {"legs": 5, "min_confidence": 56},
    DifficultyLevel.AGGRESSIVE: {"legs": 7, "min_confidence": 50},
}

STAT_WEIGHTS = {
    "PASS_YDS": 1.0,
    "PASS_TD": 0.9,
    "RUSH_YDS": 1.0,
    "REC_YDS": 1.0,
    "RECEPTIONS": 1.0,
    "TD": 0.8,
}


def calculate_nfl_projection(player_name, stat_type, recent_stats, line_info=None, injuries=None, weather=None):
    """Project a player stat from recent production, line context, injuries, and weather."""
    player_stats = recent_stats.get(player_name, {})
    values = [float(value) for value in player_stats.get(stat_type, []) if value is not None]
    if values:
        projection = mean(values[-5:])
    elif line_info and line_info.get("line") is not None:
        projection = float(line_info["line"]) * 1.04
    else:
        projection = 1.0 if stat_type in {"TD", "PASS_TD", "RECEPTIONS"} else 50.0

    injured_names = {str(row.get("player", "")).lower() for row in injuries or [] if row.get("status") not in {None, "Probable"}}
    if player_name.lower() in injured_names:
        projection *= 0.82

    wind_mph = (weather or {}).get("wind_mph")
    if wind_mph and float(wind_mph) >= 15 and stat_type in {"PASS_YDS", "PASS_TD", "REC_YDS"}:
        projection *= 0.94

    return round(max(projection * STAT_WEIGHTS.get(stat_type, 1.0), 0), 2)


def calculate_nfl_confidence(projection, line_info=None, recent_values=None, injuries=None, player_name=None, weather=None):
    """Score confidence using edge versus line, sample size, variance, injuries, and weather."""
    confidence = 54.0
    line = line_info.get("line") if line_info else None
    if line is not None:
        edge_pct = abs(float(projection) - float(line)) / max(abs(float(line)), 1.0)
        confidence += min(edge_pct * 120, 18)
    values = [float(value) for value in (recent_values or []) if value is not None]
    confidence += min(len(values), 5) * 1.5
    if len(values) >= 3:
        avg = mean(values)
        variance = mean([(value - avg) ** 2 for value in values]) ** 0.5
        confidence -= min((variance / max(avg, 1)) * 15, 8)
    injured_names = {str(row.get("player", "")).lower() for row in injuries or [] if row.get("status") not in {None, "Probable"}}
    if player_name and player_name.lower() in injured_names:
        confidence -= 18
    wind_mph = (weather or {}).get("wind_mph")
    if wind_mph and float(wind_mph) >= 15:
        confidence -= 3
    return round(max(min(confidence, 88), 35), 1)


def _line_is_over(line_info):
    side = str(line_info.get("side") or "over").lower()
    return "under" not in side


def _build_candidate(player_name, stat_type, line_info, recent_stats, injuries, weather):
    projection = calculate_nfl_projection(player_name, stat_type, recent_stats, line_info, injuries, weather)
    recent_values = recent_stats.get(player_name, {}).get(stat_type, [])
    confidence = calculate_nfl_confidence(projection, line_info, recent_values, injuries, player_name, weather)
    line = line_info.get("line")
    side = "over" if line is None or projection >= float(line) else "under"
    if line_info.get("side") and not _line_is_over(line_info):
        side = "under"
    team = recent_stats.get(player_name, {}).get("team")
    provider = line_info.get("provider") or line_info.get("bookmaker") or "provider"
    line_text = f" {side} {float(line):g}" if line is not None else " projected"
    return {
        "player": player_name,
        "team": team,
        "stat_type": stat_type,
        "line": line,
        "odds": line_info.get("odds"),
        "projection": projection,
        "confidence": confidence,
        "prediction": f"{player_name}{line_text} {stat_type}",
        "notes": f"Projection {projection:g} from recent stats and {provider} line context.",
    }


def _candidate_to_leg(candidate):
    return ParlayLeg(
        sport=SportType.NFL,
        player=candidate["player"],
        team=candidate.get("team"),
        stat_type=candidate["stat_type"],
        line=candidate.get("line"),
        odds=candidate.get("odds"),
        prediction=candidate["prediction"],
        confidence=candidate["confidence"],
        notes=candidate["notes"],
    )


def build_nfl_parlay(difficulty, team=None):
    difficulty = DifficultyLevel.from_input(difficulty)
    rules = DIFFICULTY_RULES[difficulty]

    games = get_nfl_games()
    props = get_nfl_player_props(team=team)
    team_lines = get_nfl_team_lines(team=team)
    recent_stats = get_nfl_player_recent_stats(team=team)
    injuries = get_nfl_injuries(team=team)
    weather = get_nfl_weather(games[0] if games else None) if games else get_nfl_weather()

    candidates = []
    for player_name, stat_lines in props.items():
        for stat_type, lines in stat_lines.items():
            for line_info in lines[:2]:
                candidates.append(_build_candidate(player_name, stat_type, line_info, recent_stats, injuries, weather))

    # Team lines are included as context today; player props remain the first functional parlay output.
    if not candidates and team_lines:
        print("NFL player props unavailable after provider fallback; team lines were loaded but no player leg could be built.")

    candidates = [row for row in candidates if row["confidence"] >= rules["min_confidence"]]
    candidates.sort(key=lambda row: (row["confidence"], row["projection"]), reverse=True)
    legs = [_candidate_to_leg(candidate) for candidate in candidates[: rules["legs"]]]

    combined_probability = 1.0
    for leg in legs:
        combined_probability *= max(min(leg.confidence / 100, 0.95), 0.01)

    estimated_odds = None
    if all(leg.odds is not None for leg in legs) and legs:
        estimated_odds = round((1 / max(combined_probability, 0.01) - 1) * 100)

    notes = "NFL parlay built from live provider data when available, with sample provider fallback for missing feeds."
    return ParlayResult(
        parlay=Parlay(sport=SportType.NFL, difficulty=difficulty, legs=legs, notes=notes),
        estimated_odds=estimated_odds,
        combined_probability=combined_probability if legs else 0,
        notes=notes,
    )


def print_nfl_parlay_result(result):
    print("\n========================")
    print(f"NFL {result.parlay.difficulty.value} PARLAY")
    print("========================")
    if not result.parlay.legs:
        print("No NFL legs found. Add live NFL stats/odds keys or broaden the selected team/player pool.")
        return
    for index, leg in enumerate(result.parlay.legs, 1):
        odds_label = f" ({leg.odds:+d})" if leg.odds is not None else ""
        line_label = f" | Line: {leg.line}" if leg.line is not None else " | No live line"
        print(f"{index}. {leg.prediction}{odds_label}{line_label} | Confidence: {leg.confidence:.0f}%")
        print(f"   {leg.notes}")
    if result.estimated_odds is not None:
        print(f"\nEstimated odds proxy: {result.estimated_odds:+d}")
    print(f"Combined confidence proxy: {result.combined_probability * 100:.1f}%")
    print(result.notes)


def run_nfl_parlay_flow():
    print("\nNFL Parlay Builder")
    print("1. Safe")
    print("2. Balanced")
    print("3. Aggressive")
    difficulty = input("Choose difficulty 1, 2, or 3: ").strip() or "2"
    team = input("Optional NFL team abbreviation filter (press Enter for all available providers): ").strip().upper() or None
    try:
        result = build_nfl_parlay(difficulty, team=team)
    except ValueError as exc:
        print(exc)
        return None
    print_nfl_parlay_result(result)
    parlay_id = save_parlay_result(result)
    print(f"Saved NFL parlay history row #{parlay_id} to predictions.db.")
    return result
