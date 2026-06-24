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
    DifficultyLevel.SAFE: {"min_legs": 2, "max_legs": 3, "min_confidence": 62},
    DifficultyLevel.BALANCED: {"min_legs": 4, "max_legs": 5, "min_confidence": 56},
    DifficultyLevel.AGGRESSIVE: {"min_legs": 6, "max_legs": 8, "min_confidence": 50},
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

    injured_names = {
        str(row.get("player", "")).lower()
        for row in injuries or []
        if row.get("status") not in {None, "Probable"}
    }
    if player_name.lower() in injured_names:
        projection *= 0.82

    wind_mph = (weather or {}).get("wind_mph")
    if wind_mph and float(wind_mph) >= 15 and stat_type in {"PASS_YDS", "PASS_TD", "REC_YDS"}:
        projection *= 0.94

    return round(max(projection * STAT_WEIGHTS.get(stat_type, 1.0), 0), 2)


def calculate_edge_score(projection, sportsbook_line):
    """Return the absolute projection-vs-line edge for an NFL prop candidate."""
    if sportsbook_line is None:
        return 0.0
    return round(abs(float(projection) - float(sportsbook_line)), 2)


def calculate_player_consistency(recent_values):
    """Score player consistency from recent-game variance on a 0-100 scale."""
    values = [float(value) for value in (recent_values or []) if value is not None]
    if len(values) < 2:
        return 50.0
    avg = mean(values)
    std_dev = mean([(value - avg) ** 2 for value in values]) ** 0.5
    coefficient = std_dev / max(abs(avg), 1.0)
    return round(max(100 - (coefficient * 120), 35), 1)


def calculate_matchup_adjustment(player_context=None, line_info=None):
    """Estimate matchup impact from provider context while preserving neutral fallback behavior."""
    context = {**(player_context or {}), **(line_info or {})}
    raw = context.get("matchup_difficulty") or context.get("defense_rank") or context.get("opponent_rank")
    if raw is None:
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        label = str(raw).strip().lower()
        if label in {"easy", "favorable", "plus"}:
            return 4.0
        if label in {"hard", "difficult", "tough", "minus"}:
            return -4.0
        return 0.0
    if 1 <= value <= 32:
        return round(max(min((16.5 - value) / 16.5 * 5, 5), -5), 1)
    return round(max(min(value, 5), -5), 1)


def calculate_weather_adjustment(weather=None, stat_type=None):
    """Estimate weather impact for NFL props, especially passing and receiving props."""
    if not weather:
        return 0.0
    adjustment = 0.0
    wind_mph = weather.get("wind_mph")
    if wind_mph is not None:
        wind = float(wind_mph)
        if wind >= 20:
            adjustment -= 6 if stat_type in {"PASS_YDS", "PASS_TD", "REC_YDS", "RECEPTIONS"} else 2
        elif wind >= 15:
            adjustment -= 3 if stat_type in {"PASS_YDS", "PASS_TD", "REC_YDS", "RECEPTIONS"} else 1
    condition = str(weather.get("condition") or "").lower()
    if any(token in condition for token in ("rain", "snow", "storm")):
        adjustment -= 3 if stat_type in {"PASS_YDS", "PASS_TD", "REC_YDS", "RECEPTIONS"} else 1
    return round(max(adjustment, -8), 1)


def calculate_injury_adjustment(injuries=None, player_name=None):
    """Estimate injury impact for the player named in a candidate."""
    if not player_name:
        return 0.0
    for row in injuries or []:
        if str(row.get("player", "")).lower() != player_name.lower():
            continue
        status = str(row.get("status") or "").strip().lower()
        if status in {"", "probable", "active"}:
            return 0.0
        if status in {"questionable", "limited"}:
            return -7.0
        if status in {"doubtful", "out", "ir"}:
            return -18.0
        return -10.0
    return 0.0


def _odds_value_score(odds):
    if odds is None:
        return 0.0
    odds = int(odds)
    if odds > 0:
        return min(odds / 25, 8)
    return max((odds + 110) / 20, -6)


def calculate_nfl_confidence(
    projection,
    line_info=None,
    recent_values=None,
    injuries=None,
    player_name=None,
    weather=None,
    player_context=None,
    stat_type=None,
):
    """Score confidence using edge, consistency, matchup, injuries, weather, and odds value."""
    line = line_info.get("line") if line_info else None
    edge_score = calculate_edge_score(projection, line)
    edge_pct = edge_score / max(abs(float(line)), 1.0) if line is not None else 0.0
    consistency = calculate_player_consistency(recent_values)
    matchup = calculate_matchup_adjustment(player_context, line_info)
    injury = calculate_injury_adjustment(injuries, player_name)
    weather_adjustment = calculate_weather_adjustment(weather, stat_type)
    odds_value = _odds_value_score((line_info or {}).get("odds"))

    confidence = 48.0
    confidence += min(edge_pct * 140, 22)
    confidence += min(len([v for v in (recent_values or []) if v is not None]), 5) * 1.2
    confidence += (consistency - 50) * 0.22
    confidence += matchup + injury + weather_adjustment + odds_value
    return round(max(min(confidence, 92), 30), 1)


def _line_is_over(line_info):
    side = str(line_info.get("side") or "over").lower()
    return "under" not in side


def _build_candidate(player_name, stat_type, line_info, recent_stats, injuries, weather):
    projection = calculate_nfl_projection(player_name, stat_type, recent_stats, line_info, injuries, weather)
    recent_values = recent_stats.get(player_name, {}).get(stat_type, [])
    player_context = recent_stats.get(player_name, {})
    confidence = calculate_nfl_confidence(
        projection, line_info, recent_values, injuries, player_name, weather, player_context, stat_type
    )
    edge_score = calculate_edge_score(projection, line_info.get("line"))
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
        "edge_score": edge_score,
        "consistency_score": calculate_player_consistency(recent_values),
        "prediction": f"{player_name}{line_text} {stat_type}",
        "notes": (
            f"Projection {projection:g}; edge {edge_score:g} vs {provider} line, "
            "with confidence adjusted for consistency, matchup, injuries, weather, and odds."
        ),
    }


def _selection_score(candidate, difficulty):
    """Rank NFL legs before parlay creation using style-specific edge weighting."""
    confidence = float(candidate.get("confidence") or 0)
    edge = float(candidate.get("edge_score") or 0)
    consistency = float(candidate.get("consistency_score") or 50)
    odds_value = _odds_value_score(candidate.get("odds"))
    if difficulty == DifficultyLevel.SAFE:
        return (confidence * 1.4) + (edge * 0.8) + (consistency * 0.25)
    if difficulty == DifficultyLevel.BALANCED:
        return (confidence * 1.1) + (edge * 0.9) + (odds_value * 2.0)
    variance_upside = max(100 - consistency, 0)
    return (confidence * 0.9) + (edge * 1.25) + (odds_value * 2.5) + (variance_upside * 0.25)


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
    candidates.sort(key=lambda row: _selection_score(row, difficulty), reverse=True)
    target_legs = min(rules["max_legs"], len(candidates))
    legs = [_candidate_to_leg(candidate) for candidate in candidates[:target_legs]]

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
