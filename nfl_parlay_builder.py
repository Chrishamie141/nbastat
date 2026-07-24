"""NFL parlay builder using live providers first and sample data as fallback."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import mean
import json
import math
import os
import re

from models import DifficultyLevel, Parlay, ParlayLeg, ParlayResult, SportType
from nfl_data_service import (
    NFL_SAMPLE_LINES,
    NFL_SAMPLE_PLAYERS,
    NFL_SAMPLE_RECENT_STATS,
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



@dataclass
class CandidateEvaluation:
    player: str
    provider: str | None
    sample_offline: bool
    raw_stat_type: object
    normalized_stat_type: str
    raw_line: object
    sanitized_line: float | None
    raw_odds: object
    sanitized_odds: int | None
    recent_stats_before_merge: dict
    recent_stats_after_merge: dict
    usable_recent_values: list[float]
    recent_sample_count: int
    projection: float | None
    confidence: float | None
    edge: float | None
    injury_adjustment: float
    weather_adjustment: float
    eligibility_status: str
    rejection_reasons: list[str] = field(default_factory=list)
    candidate: dict | None = None


def _debug_nfl_candidates_enabled():
    return os.getenv("SMARTBETS_DEBUG_NFL_CANDIDATES") == "1"


def _print_candidate_evaluations(evaluations):
    if not _debug_nfl_candidates_enabled():
        return
    for evaluation in evaluations:
        payload = asdict(evaluation)
        payload.pop("candidate", None)
        print("NFL_CANDIDATE_EVALUATION " + json.dumps(payload, sort_keys=True, default=str))

STAT_ALIASES = {
    "PASSING_YARDS": "PASS_YDS",
    "PASS_YARDS": "PASS_YDS",
    "PASS_YDS": "PASS_YDS",
    "PASSINGYARDS": "PASS_YDS",
    "PLAYER_PASS_YDS": "PASS_YDS",
    "PASSING_TOUCHDOWNS": "PASS_TD",
    "PASS_TDS": "PASS_TD",
    "PASS_TD": "PASS_TD",
    "PASSINGTOUCHDOWNS": "PASS_TD",
    "RUSHING_YARDS": "RUSH_YDS",
    "RUSH_YARDS": "RUSH_YDS",
    "RUSH_YDS": "RUSH_YDS",
    "RUSHINGYARDS": "RUSH_YDS",
    "RECEIVING_YARDS": "REC_YDS",
    "RECEPTION_YARDS": "REC_YDS",
    "REC_YDS": "REC_YDS",
    "RECEIVINGYARDS": "REC_YDS",
    "RECEPTIONS": "RECEPTIONS",
    "CATCHES": "RECEPTIONS",
    "ANYTIME_TD": "TD",
    "TOUCHDOWNS": "TD",
    "TD": "TD",
    "PASSING_INTERCEPTIONS": "PASS_INT",
    "PASS_INT": "PASS_INT",
}


def _normalize_name(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _normalize_stat_type(value):
    raw = str(value or "").strip()
    key = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_").upper()
    compact = key.replace("_", "")
    return STAT_ALIASES.get(key) or STAT_ALIASES.get(compact) or key


def _usable_number(value):
    if value in (None, "", [], [None]):
        return None
    if isinstance(value, (list, tuple)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def _clean_recent_values(values):
    cleaned = []
    for value in values or []:
        number = _usable_number(value)
        if number is not None:
            cleaned.append(number)
    return cleaned


def calculate_nfl_projection(player_name, stat_type, recent_stats, line_info=None, injuries=None, weather=None):
    """Project a player stat from recent production, line context, injuries, and weather."""
    stat_type = _normalize_stat_type(stat_type)
    player_stats = recent_stats.get(player_name, {})
    values = _clean_recent_values(player_stats.get(stat_type, []))
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
    values = _clean_recent_values(recent_values)
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
    odds = _usable_number(odds)
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


def _is_sample_line(line_info):
    return str((line_info or {}).get("provider") or "").lower() == "sample"


def _recent_stats_for_candidate(player_name, stat_type, line_info, recent_stats):
    """Return stats for candidate, backfilling sample props with matching sample stats.

    Live odds remain live-first. When props are deterministic sample/offline lines,
    ESPN may still return unrelated current box-score rows. Those rows are non-empty
    provider data but not usable for sample players, so backfill only the missing
    sample player/stat fields needed by the normalized candidate pipeline.
    """
    stat_type = _normalize_stat_type(stat_type)
    candidate_key = player_name
    normalized_player = _normalize_name(player_name)
    for name in recent_stats or {}:
        if _normalize_name(name) == normalized_player:
            candidate_key = name
            break
    candidate_stats = dict((recent_stats or {}).get(candidate_key) or {})
    for key, value in list(candidate_stats.items()):
        normalized_key = _normalize_stat_type(key)
        if normalized_key != key and normalized_key not in candidate_stats:
            candidate_stats[normalized_key] = value
    if _is_sample_line(line_info):
        sample_stats = {}
        for sample_name, sample_row in NFL_SAMPLE_RECENT_STATS.items():
            if _normalize_name(sample_name) == normalized_player:
                sample_stats = sample_row
                break
        if not candidate_stats or len(_clean_recent_values(candidate_stats.get(stat_type))) < 2:
            candidate_stats = {**sample_stats, **candidate_stats}
        for key, value in sample_stats.items():
            normalized_key = _normalize_stat_type(key)
            if normalized_key not in candidate_stats or len(_clean_recent_values(candidate_stats.get(normalized_key))) < 2:
                candidate_stats[normalized_key] = value
    merged = dict(recent_stats or {})
    if candidate_stats:
        merged[player_name] = candidate_stats
    return merged


def _evaluate_candidate(player_name, stat_type, line_info, recent_stats, injuries, weather, rules=None):
    raw_stat_type = stat_type
    normalized_stat_type = _normalize_stat_type(stat_type)
    raw_line = (line_info or {}).get("line")
    raw_odds = (line_info or {}).get("odds")
    provider = (line_info or {}).get("provider") or (line_info or {}).get("bookmaker") or "provider"
    before_key = player_name
    normalized_player = _normalize_name(player_name)
    for name in recent_stats or {}:
        if _normalize_name(name) == normalized_player:
            before_key = name
            break
    recent_stats_before_merge = dict((recent_stats or {}).get(before_key) or {})

    cleaned_line = dict(line_info or {})
    cleaned_line["line"] = _usable_number(cleaned_line.get("line"))
    if _is_sample_line(cleaned_line) and cleaned_line["line"] is None:
        for sample_name, sample_lines in NFL_SAMPLE_LINES.items():
            if _normalize_name(sample_name) != normalized_player:
                continue
            sample_stat_lines = sample_lines.get(normalized_stat_type) or []
            for sample_line in sample_stat_lines:
                sample_value = _usable_number(sample_line.get("line"))
                if sample_value is not None:
                    cleaned_line["line"] = sample_value
                    break
            break
    cleaned_odds = _usable_number(cleaned_line.get("odds"))
    cleaned_line["odds"] = int(cleaned_odds) if cleaned_odds is not None else None

    merged_stats = _recent_stats_for_candidate(player_name, normalized_stat_type, cleaned_line, recent_stats)
    recent_stats_after_merge = dict(merged_stats.get(player_name, {}) or {})
    recent_values = _clean_recent_values(recent_stats_after_merge.get(normalized_stat_type, []))
    rejection_reasons = []
    projection = confidence = edge_score = None
    injury_adjustment = calculate_injury_adjustment(injuries, player_name)
    weather_adjustment = calculate_weather_adjustment(weather, normalized_stat_type)

    if cleaned_line["line"] is None:
        rejection_reasons.append("invalid_line")
    if cleaned_line["odds"] is None:
        rejection_reasons.append("invalid_odds")
    if normalized_stat_type not in STAT_WEIGHTS:
        rejection_reasons.append("unsupported_stat_type")
    if not recent_stats_after_merge or normalized_stat_type not in recent_stats_after_merge:
        rejection_reasons.append("missing_recent_stats")
    if len(recent_values) < 2:
        rejection_reasons.append("insufficient_recent_sample")

    if injury_adjustment <= -18:
        rejection_reasons.append("injury_disqualification")
    if weather_adjustment <= -8:
        rejection_reasons.append("weather_disqualification")

    if not any(reason in rejection_reasons for reason in ("invalid_line", "unsupported_stat_type")):
        try:
            projection = calculate_nfl_projection(player_name, normalized_stat_type, merged_stats, cleaned_line, injuries, weather)
        except (TypeError, ValueError, OSError):
            rejection_reasons.append("projection_failed")
    else:
        rejection_reasons.append("projection_failed")

    if projection is not None:
        confidence = calculate_nfl_confidence(
            projection, cleaned_line, recent_values, injuries, player_name, weather, recent_stats_after_merge, normalized_stat_type
        )
        edge_score = calculate_edge_score(projection, cleaned_line.get("line"))
        if rules and confidence < rules["min_confidence"]:
            rejection_reasons.append("confidence_below_minimum")
        if edge_score <= 0:
            rejection_reasons.append("edge_below_minimum")

    status = "eligible" if not rejection_reasons else "rejected"
    candidate = None
    if status == "eligible":
        line = cleaned_line.get("line")
        side = "over" if line is None or projection >= float(line) else "under"
        if cleaned_line.get("side") and not _line_is_over(cleaned_line):
            side = "under"
        team = recent_stats_after_merge.get("team")
        line_text = f" {side} {float(line):g}" if line is not None else " projected"
        data_label = " Sample/offline fallback data." if _is_sample_line(cleaned_line) else ""
        candidate = {
            "player": player_name,
            "team": team,
            "stat_type": normalized_stat_type,
            "line": line,
            "odds": cleaned_line.get("odds"),
            "projection": projection,
            "confidence": confidence,
            "edge_score": edge_score,
            "consistency_score": calculate_player_consistency(recent_values),
            "prediction": f"{player_name}{line_text} {normalized_stat_type}",
            "notes": (
                f"Projection {projection:g}; edge {edge_score:g} vs {provider} line, "
                "with confidence adjusted for consistency, matchup, injuries, weather, and odds."
                f"{data_label}"
            ),
            "sample_offline": _is_sample_line(cleaned_line),
        }

    return CandidateEvaluation(
        player=player_name, provider=provider, sample_offline=_is_sample_line(cleaned_line),
        raw_stat_type=raw_stat_type, normalized_stat_type=normalized_stat_type, raw_line=raw_line,
        sanitized_line=cleaned_line.get("line"), raw_odds=raw_odds, sanitized_odds=cleaned_line.get("odds"),
        recent_stats_before_merge=recent_stats_before_merge, recent_stats_after_merge=recent_stats_after_merge,
        usable_recent_values=recent_values, recent_sample_count=len(recent_values), projection=projection,
        confidence=confidence, edge=edge_score, injury_adjustment=injury_adjustment,
        weather_adjustment=weather_adjustment, eligibility_status=status, rejection_reasons=rejection_reasons,
        candidate=candidate,
    )


def _build_candidate(player_name, stat_type, line_info, recent_stats, injuries, weather):
    return _evaluate_candidate(player_name, stat_type, line_info, recent_stats, injuries, weather).candidate

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


def _team_filter_excludes_sample_players(team):
    if not team:
        return False
    team_key = str(team).strip().upper()
    return not any(player.get("team") == team_key for player in NFL_SAMPLE_PLAYERS)


def build_nfl_parlay(difficulty, team=None):
    difficulty = DifficultyLevel.from_input(difficulty)
    rules = DIFFICULTY_RULES[difficulty]

    games = get_nfl_games()
    props = get_nfl_player_props(team=team)
    team_lines = get_nfl_team_lines(team=team)
    recent_stats = get_nfl_player_recent_stats(team=team)
    injuries = get_nfl_injuries(team=team)
    weather = get_nfl_weather(games[0] if games else None) if games else get_nfl_weather()

    evaluations = []
    for player_name, stat_lines in props.items():
        for stat_type, lines in stat_lines.items():
            for line_info in lines[:2]:
                evaluations.append(_evaluate_candidate(player_name, stat_type, line_info, recent_stats, injuries, weather, rules))

    candidates = [evaluation.candidate for evaluation in evaluations if evaluation.candidate]

    _print_candidate_evaluations(evaluations)

    # Team lines are included as context today; player props remain the first functional parlay output.
    if not candidates and team_lines:
        print("NFL player props unavailable after provider fallback; team lines were loaded but no player leg could be built.")

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
    if not legs and _team_filter_excludes_sample_players(team):
        notes = (
            f"{notes} Team filter {str(team).strip().upper()} did not match fallback/sample NFL players; "
            "try running without a team filter."
        )
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
        if "try running without a team filter" in (result.notes or ""):
            print("Team filter removed all fallback/sample players; try running without a team filter.")
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
    if not result.parlay.legs:
        print("No NFL parlay saved because no legs were generated.")
        return result
    parlay_id = save_parlay_result(result)
    print(f"Saved NFL parlay history row #{parlay_id} to predictions.db.")
    return result
