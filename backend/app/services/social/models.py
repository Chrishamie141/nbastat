"""Shared social-engine value objects and allowlists."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


EVENT_TYPES = (
    "SLATE_READY", "PREDICTION_READY", "HIGH_CONFIDENCE_PREDICTION",
    "MODEL_MARKET_DISAGREEMENT", "LINE_MOVEMENT", "GAME_STARTING_SOON",
    "PRIMETIME_GAME", "UPSET_WATCH", "GAME_FINAL", "PREDICTION_WIN",
    "PREDICTION_LOSS", "PREDICTION_PUSH", "DAILY_SLATE_COMPLETE",
    "WEEKLY_SLATE_COMPLETE", "STREAK_MILESTONE",
)

CONTENT_TYPES = (
    "AI_PICK", "TODAYS_CARD", "MODEL_VS_MARKET", "UPSET_WATCH",
    "LINE_MOVEMENT", "CASHED", "WIN_RECEIPT", "MISS", "LOSS_RECEIPT",
    "DAILY_RECAP", "WEEKLY_REPORT", "STREAK_MILESTONE", "GAME_PREVIEW",
    "ENGAGEMENT_QUESTION", "PLAYER_SPOTLIGHT", "MODEL_RECAP", "TREND",
    "PRODUCT_AWARENESS", "WATCHLIST", "ENGAGEMENT",
)


class OpportunityDecision(StrEnum):
    POST_NOW = "POST_NOW"
    QUEUE = "QUEUE"
    SKIP = "SKIP"
    REVIEW = "REVIEW"


class QueueStatus(StrEnum):
    DRAFT = "DRAFT"
    MEDIA_GENERATING = "MEDIA_GENERATING"
    READY = "READY"
    QUEUED = "QUEUED"
    REVIEW = "REVIEW"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class ScoreResult:
    score: float
    threshold: float
    decision: OpportunityDecision
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "threshold": self.threshold,
            "decision": self.decision.value,
            "reasons": list(self.reasons),
        }
