"""Research-only NBA game-winner baseline.

The platform did not contain an NBA game-winner model.  This deterministic Elo
baseline makes the lifecycle executable without representing an unvalidated
model as production-ready.  Promotion remains governed by the NBA registry.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math


NBA_GAME_MODEL_VERSION = "nba-game-elo-baseline-v1"
INITIAL_RATING = 1500.0
HOME_ADVANTAGE = 65.0
K_FACTOR = 20.0


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _expected(home_rating: float, away_rating: float) -> float:
    return 1.0 / (1.0 + math.pow(10.0, -((home_rating + HOME_ADVANTAGE) - away_rating) / 400.0))


def update_ratings(ratings: dict[str, float], game: dict) -> None:
    """Apply one verified final to ratings in chronological order."""
    try:
        home_score = int(game["home_score"])
        away_score = int(game["away_score"])
    except (KeyError, TypeError, ValueError):
        return
    home, away = str(game["home_team"]), str(game["away_team"])
    home_rating = ratings.get(home, INITIAL_RATING)
    away_rating = ratings.get(away, INITIAL_RATING)
    expected_home = _expected(home_rating, away_rating)
    actual_home = 0.5 if home_score == away_score else 1.0 if home_score > away_score else 0.0
    change = K_FACTOR * (actual_home - expected_home)
    ratings[home] = home_rating + change
    ratings[away] = away_rating - change


def winner_prediction(game: dict, *, ratings: dict[str, float], evidence: list[dict],
                      generated_at: str) -> dict:
    """Build a transparent, pregame research snapshot for one scheduled game."""
    kickoff = _dt(game["kickoff_time"])
    generated = _dt(generated_at)
    if generated >= kickoff:
        raise ValueError("NBA game-winner predictions must be generated before tipoff")
    home, away = str(game["home_team"]), str(game["away_team"])
    home_probability = _expected(
        ratings.get(home, INITIAL_RATING), ratings.get(away, INITIAL_RATING)
    )
    winner = home if home_probability >= 0.5 else away
    evidence_ids = [str(row["source_hash"]) for row in evidence if row.get("source_hash")]
    data_version = hashlib.sha256(
        json.dumps(evidence_ids, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "game_id": str(game["game_id"]), "season": str(game["season_code"]),
        "kickoff_time": str(game["kickoff_time"]), "market": "moneyline",
        "selection": winner, "line": None,
        "model_probability": round(home_probability if winner == home else 1.0 - home_probability, 6),
        "projection": None, "edge": None, "market_odds": None,
        "model_version": NBA_GAME_MODEL_VERSION, "data_version": data_version,
        "generated_at": generated_at, "feature_cutoff": generated_at,
        "source_provider": "espn_scoreboard",
        "model_status": "RESEARCH_ONLY", "wager_authorized": False,
        "home_probability": round(home_probability, 6),
        "away_probability": round(1.0 - home_probability, 6),
        "evidence_games": len(evidence),
        "methodology": "research-only chronological Elo baseline; not production-approved",
    }
