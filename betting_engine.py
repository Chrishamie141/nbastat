"""Betting recommendation helpers for NBA player stat projections."""

from __future__ import annotations

import math
from functools import reduce
from operator import mul

try:
    from scipy.stats import norm
except ImportError:  # pragma: no cover - exercised when scipy is unavailable
    norm = None

VOLATILE_STATS = {"3PM", "STL", "BLK"}
STAT_MINIMUM_STD = {
    "PTS": 5.5,
    "REB": 3.0,
    "AST": 2.2,
    "STL": 1.0,
    "BLK": 1.0,
    "3PM": 1.5,
}
STAT_PROBABILITY_CAPS = {
    "PTS": 0.88,
    "REB": 0.86,
    "AST": 0.84,
    "STL": 0.72,
    "BLK": 0.72,
    "3PM": 0.70,
}
NEAR_PROJECTION_CAPS = {
    "PTS": 0.58,
    "REB": 0.57,
    "AST": 0.56,
}
MIN_HIT_PROBABILITY = 0.03
PARLAY_SIZE_PENALTIES = {
    3: 0.90,
    4: 0.82,
    5: 0.74,
    6: 0.66,
    7: 0.58,
    8: 0.50,
    9: 0.43,
    10: 0.36,
    11: 0.30,
    12: 0.25,
}

STAT_MINIMUM_PROJECTIONS = {
    "PTS": 5.0,
    "REB": 3.0,
    "AST": 2.0,
    "STL": 0.8,
    "BLK": 0.8,
}


def american_odds_to_implied_probability(odds):
    """Convert American odds into break-even implied probability."""
    odds = float(odds)
    if odds == 0:
        raise ValueError("American odds cannot be 0.")
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)


def implied_probability_to_american_odds(probability):
    """Convert probability in 0..1 into American odds."""
    probability = float(probability)
    if probability <= 0 or probability >= 1:
        raise ValueError("Probability must be between 0 and 1.")
    if probability > 0.5:
        return round(-(probability / (1 - probability)) * 100)
    return round(((1 - probability) / probability) * 100)


def _normal_cdf(value, mean, std):
    if std <= 0:
        return 1.0 if value >= mean else 0.0
    if norm is not None:
        return float(norm.cdf(value, loc=mean, scale=std))
    z_score = (value - mean) / (std * math.sqrt(2))
    return 0.5 * (1 + math.erf(z_score))


def estimate_hit_probability(projection, low_range, high_range, target_line, stat_type=None):
    """Estimate P(stat >= line) with conservative normal-distribution guardrails.

    The projection range is intentionally widened into a larger standard
    deviation than the raw model interval implies. Stat-specific minimum
    standard deviations and probability caps prevent alternate lines near the
    projection from being treated as near-certainties.
    """
    stat_key = str(stat_type or "").upper()
    projection = float(projection)
    low_range = float(low_range)
    high_range = float(high_range)
    target_line = float(target_line)

    range_width = max(high_range - low_range, 0.0)
    base_std = range_width / 2.8 if range_width else 0.01
    stat_min_std = STAT_MINIMUM_STD.get(stat_key, 1.0)
    final_std = max(base_std, stat_min_std)

    probability = 1 - _normal_cdf(target_line, projection, final_std)

    cap = STAT_PROBABILITY_CAPS.get(stat_key, 0.88)
    if target_line >= projection:
        cap = min(cap, 0.52)
    if abs(target_line - projection) <= 1:
        cap = min(cap, NEAR_PROJECTION_CAPS.get(stat_key, cap))
    if target_line > projection + 2:
        cap = min(cap, 0.45)

    return min(max(probability, MIN_HIT_PROBABILITY), cap)


def calculate_edge(model_probability, sportsbook_probability):
    """Return model edge over sportsbook break-even probability."""
    return float(model_probability) - float(sportsbook_probability)


def classify_bet_strength(edge, model_probability, confidence):
    """Classify a prop by edge, hit probability, and prediction confidence."""
    edge = float(edge)
    model_probability = float(model_probability)
    confidence = float(confidence or 0)

    if edge < 0 or model_probability < 0.45 or confidence < 45:
        return "AVOID"
    if edge >= 0.10 and model_probability >= 0.60 and confidence >= 70:
        return "STRONG"
    if edge >= 0.04 and model_probability >= 0.55 and confidence >= 60:
        return "MEDIUM"
    if edge >= 0 and model_probability >= 0.50:
        return "LIGHT"
    return "AVOID"


def _normalize_name(name):
    return " ".join(str(name).lower().replace(".", "").split())


def _line_is_meaningful(prediction, edge):
    stat = str(prediction.get("stat_type", "")).upper()
    projection = float(prediction.get("projection", 0) or 0)
    minimum = STAT_MINIMUM_PROJECTIONS.get(stat)
    if minimum is None:
        return True
    if projection >= minimum:
        return True
    return stat in {"STL", "BLK"} and edge >= 0.12


def sportsbook_line_coverage(predictions, sportsbook_lines):
    """Summarize how completely sportsbook lines cover generated predictions."""
    normalized_lines = {_normalize_name(player): lines for player, lines in sportsbook_lines.items()}
    matched_lines = 0
    missing_predictions = 0
    total_candidate_lines = 0
    players_without_lines = set()
    players_with_partial_lines = set()

    for lines_by_stat in sportsbook_lines.values():
        for stat_lines in lines_by_stat.values():
            total_candidate_lines += len(stat_lines or [])

    predictions_by_player = {}
    for prediction in predictions:
        player = str(prediction.get("player"))
        predictions_by_player.setdefault(player, []).append(prediction)

    for player, player_predictions in predictions_by_player.items():
        player_lines = normalized_lines.get(_normalize_name(player))
        player_matched_lines = 0
        player_missing_predictions = 0

        if not player_lines:
            missing_predictions += len(player_predictions)
            players_without_lines.add(player)
            continue

        for prediction in player_predictions:
            stat_type = str(prediction.get("stat_type", "")).upper()
            stat_lines = player_lines.get(stat_type, [])
            if stat_lines:
                stat_line_count = len(stat_lines)
                matched_lines += stat_line_count
                player_matched_lines += stat_line_count
            else:
                missing_predictions += 1
                player_missing_predictions += 1

        if player_matched_lines and player_missing_predictions:
            players_with_partial_lines.add(player)
        elif not player_matched_lines:
            players_without_lines.add(player)

    return {
        "matched_lines": matched_lines,
        "missing_predictions": missing_predictions,
        "players_without_lines": sorted(players_without_lines),
        "players_with_partial_lines": sorted(players_with_partial_lines),
        "total_prediction_rows": len(predictions),
        "total_candidate_lines": total_candidate_lines,
    }


def print_line_coverage_warning(coverage, threshold):
    """Print line coverage diagnostics before betting/parlay reports."""
    players = coverage.get("players_without_lines", [])
    partial_players = coverage.get("players_with_partial_lines", [])
    print(f"Total prediction rows: {coverage.get('total_prediction_rows', 0)}")
    print(f"Total sportsbook candidate lines: {coverage.get('total_candidate_lines', 0)}")
    print(f"Matched sportsbook lines: {coverage.get('matched_lines', 0)}")
    print(f"Missing sportsbook lines: {coverage.get('missing_predictions', 0)}")
    print(f"Players without lines: {', '.join(players) if players else 'None'}")
    print(f"Players with partial lines: {', '.join(partial_players) if partial_players else 'None'}")
    if coverage.get("matched_lines", 0) < threshold:
        print("WARNING: betting_lines.json may be too incomplete to build a meaningful parlay.")


def recommend_bets(predictions, sportsbook_lines):
    """Match predictions to sportsbook lines and rank recommendations by edge."""
    normalized_lines = {_normalize_name(player): lines for player, lines in sportsbook_lines.items()}
    recommendations = []

    for prediction in predictions:
        player = prediction.get("player")
        stat_type = str(prediction.get("stat_type", "")).upper()
        player_lines = normalized_lines.get(_normalize_name(player), {})
        stat_lines = player_lines.get(stat_type, [])
        if not stat_lines:
            continue

        projection = prediction.get("projection")
        low_range = prediction.get("low_range")
        high_range = prediction.get("high_range")
        confidence = prediction.get("confidence_score", 0)

        for line_info in stat_lines:
            line = float(line_info["line"])
            odds = int(line_info["odds"])
            model_probability = estimate_hit_probability(
                projection, low_range, high_range, line, stat_type
            )
            sportsbook_probability = american_odds_to_implied_probability(odds)
            edge = calculate_edge(model_probability, sportsbook_probability)
            strength = classify_bet_strength(edge, model_probability, confidence)
            meaningful = _line_is_meaningful(prediction, edge)
            recommendations.append(
                {
                    "prediction_id": prediction.get("prediction_id"),
                    "player": player,
                    "team": prediction.get("team"),
                    "opponent": prediction.get("opponent"),
                    "game_date": prediction.get("game_date"),
                    "stat_type": stat_type,
                    "line": line,
                    "sportsbook_odds": odds,
                    "sportsbook_probability": sportsbook_probability,
                    "model_probability": model_probability,
                    "edge": edge,
                    "strength": strength if meaningful else "AVOID",
                    "recommended": bool(meaningful and edge > 0 and strength != "AVOID"),
                    "projection": float(projection),
                    "low_range": float(low_range),
                    "high_range": float(high_range),
                    "confidence_score": float(confidence or 0),
                }
            )

    return sorted(
        recommendations,
        key=lambda bet: (bet["recommended"], bet["edge"], bet["model_probability"]),
        reverse=True,
    )


def _american_to_decimal(odds):
    odds = float(odds)
    if odds < 0:
        return 1 + 100 / abs(odds)
    return 1 + odds / 100


def _decimal_to_american(decimal_odds):
    if decimal_odds >= 2:
        return round((decimal_odds - 1) * 100)
    return round(-100 / (decimal_odds - 1))


def _parlay_rules(style):
    rules = {
        "SAFE": {"min_legs": 3, "max_legs": 5, "min_prob": 0.60, "min_edge": 0.0},
        "BALANCED": {"min_legs": 5, "max_legs": 8, "min_prob": 0.52, "min_edge": -0.03},
        "AGGRESSIVE": {"min_legs": 8, "max_legs": 12, "min_prob": 0.45, "min_edge": -0.05},
    }
    style_key = str(style or "balanced").strip().upper()
    if style_key not in rules:
        raise ValueError("Parlay style must be SAFE, BALANCED, or AGGRESSIVE.")
    return style_key, rules[style_key]


def _parlay_line_threshold(style_key):
    return {"SAFE": 5, "BALANCED": 8, "AGGRESSIVE": 10}[style_key]


def parlay_line_threshold(style):
    """Return minimum matching sportsbook lines needed for a useful parlay style."""
    style_key, _ = _parlay_rules(style)
    return _parlay_line_threshold(style_key)


def _qualifies_for_parlay(bet, style_key, rule, moderate_risk_used=False):
    if bet.get("strength") == "AVOID":
        return False, "not enough positive edge bets"
    if bet["model_probability"] < rule["min_prob"]:
        return False, "probability threshold too strict"
    if bet["edge"] < rule["min_edge"]:
        return False, "not enough positive edge bets"
    if style_key == "SAFE" and bet["stat_type"] in VOLATILE_STATS and not (
        bet["edge"] >= 0.08 and bet["model_probability"] >= 0.65
    ):
        return False, "probability threshold too strict"
    if style_key == "BALANCED" and bet["edge"] < 0 and moderate_risk_used:
        return False, "not enough positive edge bets"
    if style_key == "AGGRESSIVE" and bet["edge"] < -0.03 and bet["model_probability"] < 0.50:
        return False, "probability threshold too strict"
    return True, None


def _risk_from_adjusted_probability(adjusted_probability):
    if adjusted_probability >= 0.25:
        return "MEDIUM"
    if adjusted_probability >= 0.10:
        return "MEDIUM-HIGH"
    if adjusted_probability >= 0.03:
        return "HIGH"
    return "VERY HIGH"


def build_parlay(recommended_bets, style, coverage=None):
    """Build a parlay from ranked bet recommendations for a risk style."""
    style_key, rule = _parlay_rules(style)
    selected = []
    used_players_stats = set()
    moderate_risk_used = False
    rejection_reasons = {
        "not enough sportsbook lines": 0,
        "not enough positive edge bets": 0,
        "probability threshold too strict": 0,
        "duplicate player/stat already selected": 0,
    }

    if coverage and coverage.get("matched_lines", len(recommended_bets)) < _parlay_line_threshold(style_key):
        rejection_reasons["not enough sportsbook lines"] += 1

    for bet in sorted(recommended_bets, key=lambda x: (x["edge"], x["model_probability"]), reverse=True):
        qualifies, reason = _qualifies_for_parlay(bet, style_key, rule, moderate_risk_used)
        if not qualifies:
            rejection_reasons[reason] += 1
            continue

        key = (bet["player"], bet["stat_type"])
        if key in used_players_stats:
            rejection_reasons["duplicate player/stat already selected"] += 1
            continue

        if len(selected) < rule["max_legs"]:
            used_players_stats.add(key)
            selected.append(bet)
            if style_key == "BALANCED" and bet["edge"] < 0:
                moderate_risk_used = True

    raw_combined_probability = reduce(mul, (bet["model_probability"] for bet in selected), 1.0) if selected else 0.0
    size_penalty = PARLAY_SIZE_PENALTIES.get(len(selected), 1.0)
    adjusted_combined_probability = raw_combined_probability * size_penalty
    decimal_odds = reduce(mul, (_american_to_decimal(bet["sportsbook_odds"]) for bet in selected), 1.0) if selected else 1.0
    estimated_odds = _decimal_to_american(decimal_odds) if selected and decimal_odds > 1 else 0
    risk = _risk_from_adjusted_probability(adjusted_combined_probability)
    matched_lines = coverage.get("matched_lines", len(recommended_bets)) if coverage else len(recommended_bets)

    primary_reason = None
    if len(selected) < rule["min_legs"]:
        if matched_lines < _parlay_line_threshold(style_key):
            primary_reason = "not enough sportsbook lines"
        else:
            primary_reason = max(
                ((reason, count) for reason, count in rejection_reasons.items() if reason != "duplicate player/stat already selected"),
                key=lambda item: item[1],
                default=("not enough positive edge bets", 0),
            )[0]

    return {
        "style": style_key,
        "legs": selected,
        "raw_combined_probability": raw_combined_probability,
        "adjusted_combined_probability": adjusted_combined_probability,
        "combined_probability": adjusted_combined_probability,
        "parlay_size_penalty": size_penalty,
        "decimal_odds": decimal_odds,
        "estimated_odds": estimated_odds,
        "risk": risk,
        "min_legs": rule["min_legs"],
        "max_legs": rule["max_legs"],
        "complete": len(selected) >= rule["min_legs"],
        "matched_lines": matched_lines,
        "rejection_reasons": rejection_reasons,
        "primary_shortfall_reason": primary_reason,
    }
