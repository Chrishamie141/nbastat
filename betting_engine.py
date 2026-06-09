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
    """Estimate P(stat >= line) with a normal distribution approximation.

    The model range is treated as roughly a 95% interval, so four standard
    deviations span low-to-high. For displayed alternate lines like 20+ points,
    the target line is used directly and the returned probability is the upper
    tail of the normal curve: 1 - CDF(line).
    """
    projection = float(projection)
    low_range = float(low_range)
    high_range = float(high_range)
    target_line = float(target_line)
    std = max((high_range - low_range) / 4, 0.01)
    probability = 1 - _normal_cdf(target_line, projection, std)
    return min(max(probability, 0.0), 1.0)


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


def build_parlay(recommended_bets, style):
    """Build a parlay from ranked bet recommendations for a risk style."""
    style_key = str(style or "balanced").strip().upper()
    rules = {
        "SAFE": {"min_legs": 3, "max_legs": 5, "min_prob": 0.60, "min_edge": 0.0},
        "BALANCED": {"min_legs": 5, "max_legs": 8, "min_prob": 0.52, "min_edge": -0.03},
        "AGGRESSIVE": {"min_legs": 8, "max_legs": 12, "min_prob": 0.45, "min_edge": -0.05},
    }
    if style_key not in rules:
        raise ValueError("Parlay style must be SAFE, BALANCED, or AGGRESSIVE.")

    rule = rules[style_key]
    selected = []
    used_players_stats = set()
    moderate_risk_used = False

    for bet in sorted(recommended_bets, key=lambda x: (x["edge"], x["model_probability"]), reverse=True):
        if len(selected) >= rule["max_legs"]:
            break
        if bet.get("strength") == "AVOID":
            continue
        if bet["model_probability"] < rule["min_prob"] or bet["edge"] < rule["min_edge"]:
            continue
        if style_key == "SAFE" and bet["stat_type"] in VOLATILE_STATS and not (
            bet["edge"] >= 0.08 and bet["model_probability"] >= 0.65
        ):
            continue
        if style_key == "BALANCED" and bet["edge"] < 0:
            if moderate_risk_used:
                continue
            moderate_risk_used = True
        if style_key == "AGGRESSIVE" and bet["edge"] < -0.03 and bet["model_probability"] < 0.50:
            continue

        key = (bet["player"], bet["stat_type"])
        if key in used_players_stats:
            continue
        used_players_stats.add(key)
        selected.append(bet)

    combined_probability = reduce(mul, (bet["model_probability"] for bet in selected), 1.0) if selected else 0.0
    decimal_odds = reduce(mul, (_american_to_decimal(bet["sportsbook_odds"]) for bet in selected), 1.0) if selected else 1.0
    estimated_odds = _decimal_to_american(decimal_odds) if selected and decimal_odds > 1 else 0
    risk = {"SAFE": "MEDIUM", "BALANCED": "MEDIUM-HIGH", "AGGRESSIVE": "HIGH"}[style_key]

    return {
        "style": style_key,
        "legs": selected,
        "combined_probability": combined_probability,
        "decimal_odds": decimal_odds,
        "estimated_odds": estimated_odds,
        "risk": risk,
        "min_legs": rule["min_legs"],
        "max_legs": rule["max_legs"],
        "complete": len(selected) >= rule["min_legs"],
    }
