"""Shared domain models for multi-sport prediction and parlay workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class SportType(str, Enum):
    NFL = "NFL"
    NBA = "NBA"


class DifficultyLevel(str, Enum):
    SAFE = "SAFE"
    BALANCED = "BALANCED"
    AGGRESSIVE = "AGGRESSIVE"

    @classmethod
    def from_input(cls, value: str | None) -> "DifficultyLevel":
        normalized = str(value or "BALANCED").strip().upper()
        aliases = {"1": "SAFE", "2": "BALANCED", "3": "AGGRESSIVE"}
        return cls(aliases.get(normalized, normalized))


@dataclass(frozen=True)
class PredictionResult:
    sport: SportType
    player: str
    stat_type: str
    prediction: float
    confidence: float
    low_range: Optional[float] = None
    high_range: Optional[float] = None
    team: Optional[str] = None
    opponent: Optional[str] = None
    odds: Optional[int] = None
    line: Optional[float] = None
    notes: str = ""


@dataclass(frozen=True)
class ParlayLeg:
    sport: SportType
    prediction: str
    confidence: float
    player: Optional[str] = None
    team: Optional[str] = None
    stat_type: Optional[str] = None
    line: Optional[float] = None
    odds: Optional[int] = None
    notes: str = ""


@dataclass(frozen=True)
class Parlay:
    sport: SportType
    difficulty: DifficultyLevel
    legs: list[ParlayLeg]
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )
    notes: str = ""


@dataclass(frozen=True)
class ParlayResult:
    parlay: Parlay
    estimated_odds: Optional[int] = None
    combined_probability: Optional[float] = None
    result_status: str = "pending"
    notes: str = ""
