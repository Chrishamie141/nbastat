"""Explainable deterministic content opportunity scoring."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import OpportunityDecision, ScoreResult


def score(event: dict[str, Any], settings: dict[str, Any], *, recent_posts: int = 0,
          same_game_type_posted: bool = False, similarity: float = 0,
          media_type_recently_overused: bool = False, analytics_modifier: float = 0, clock=None) -> ScoreResult:
    at = (clock or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc)
    evidence = event.get("evidence") or {}
    prediction = evidence.get("prediction") or {}
    market = evidence.get("market") or {}
    event_type = event["event_type"]
    threshold = float(settings["opportunity_score_threshold"])
    value = 35.0
    reasons: list[str] = ["Verified source evidence is available (+35)"]

    if event_type in {"PREDICTION_READY", "HIGH_CONFIDENCE_PREDICTION", "MODEL_MARKET_DISAGREEMENT",
                      "LINE_MOVEMENT", "GAME_STARTING_SOON", "GAME_FINAL"}:
        value += 20
        reasons.append("Timely game-specific event adds 20 points")
    if event_type == "SLATE_READY":
        value += 25
        reasons.append("A verified slate ready for audience review adds 25 points")

    probability = float(prediction.get("probability") or 0)
    if probability >= .65:
        bonus = min(18.0, round((probability - .50) * 80, 1))
        value += bonus
        reasons.append(f"Model confidence adds {bonus:g} points")
    edge = market.get("edge")
    if edge is not None:
        bonus = min(18.0, round(abs(float(edge)) * 100, 1))
        value += bonus
        reasons.append(f"Verified model-market disagreement adds {bonus:g} points")
    if event_type in {"PRIMETIME_GAME", "UPSET_WATCH"}:
        value += 8
        reasons.append("Marquee or upset context adds 8 points")
    if event_type in {"PREDICTION_WIN", "PREDICTION_LOSS", "PREDICTION_PUSH"}:
        value += 15
        reasons.append("Transparent final-result receipt adds 15 points")
    if event_type in {"DAILY_SLATE_COMPLETE", "WEEKLY_SLATE_COMPLETE"}:
        value += 20
        reasons.append("Completed slate reporting adds 20 points")
    if event_type == "STREAK_MILESTONE":
        value += 10
        reasons.append("Milestone significance adds 10 points")

    kickoff_raw = evidence.get("kickoff_time")
    if kickoff_raw:
        kickoff = datetime.fromisoformat(str(kickoff_raw).replace("Z", "+00:00")).astimezone(timezone.utc)
        hours = (kickoff - at).total_seconds() / 3600
        if 0 < hours <= 3:
            value += 10
            reasons.append("Kickoff is within three hours (+10)")
        elif hours < 0 and event_type not in {"GAME_FINAL", "PREDICTION_WIN", "PREDICTION_LOSS", "PREDICTION_PUSH"}:
            value -= 100
            reasons.append("Pregame opportunity expired at kickoff (-100)")
    if same_game_type_posted:
        value -= 100
        reasons.append("This game and content type were already published (-100)")
    if similarity >= .82:
        value -= 45
        reasons.append("Recent copy is too similar (-45)")
    if recent_posts >= int(settings["max_posts_per_day"]):
        value -= 100
        reasons.append("Daily post limit reached (-100)")
    elif recent_posts:
        penalty = min(18, recent_posts * 4)
        value -= penalty
        reasons.append(f"Recent publishing volume subtracts {penalty} points")
    if media_type_recently_overused:
        value -= 5
        reasons.append("Content diversity adjustment subtracts 5 points")
    if analytics_modifier:
        adjustment = max(-5.0, min(5.0, float(analytics_modifier)))
        value += adjustment
        reasons.append(f"Mature social-performance sample adjusts {adjustment:+g} points")

    value = round(max(0, min(100, value)), 1)
    if same_game_type_posted or value < max(1, threshold - 15):
        decision = OpportunityDecision.SKIP
    elif value < threshold:
        decision = OpportunityDecision.REVIEW
    elif value >= min(100, threshold + 20):
        decision = OpportunityDecision.POST_NOW
    else:
        decision = OpportunityDecision.QUEUE
    return ScoreResult(value, threshold, decision, tuple(reasons))
