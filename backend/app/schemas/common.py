from __future__ import annotations
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator

class CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

class ProviderStatus(CamelModel):
    status: Literal['live','partial_live','sample','unavailable'] = 'sample'
    configuredProviders: list[str] = Field(default_factory=list)

class TeamSummary(CamelModel):
    id: str
    name: str
    abbreviation: str
    logoUrl: str | None = None
    record: str | None = None

class UpcomingGame(CamelModel):
    id: str
    league: Literal['nfl','nba']
    seasonPhase: str
    awayTeam: TeamSummary
    homeTeam: TeamSummary
    startTimeUtc: datetime
    status: str = 'scheduled'
    venue: str | None = None
    broadcast: list[str] = Field(default_factory=list)
    nationalBroadcast: bool = False
    watchScore: int = 0
    watchReasons: list[str] = Field(default_factory=list)
    dataProvider: str = 'unavailable'
    dataMode: Literal['live','partial_live','sample','unavailable'] = 'unavailable'

class SportsModeResponse(CamelModel):
    mode: Literal['nfl','nba','both','offseason']
    activeLeagues: list[str]
    phaseByLeague: dict[str, str]
    reason: str
    effectiveAt: datetime
    nextTransition: datetime | None = None
    source: Literal['schedule','configured_calendar','manual_override','fallback']
    overrideActive: bool = False

class DashboardMetrics(CamelModel):
    savedAnalyses: int = 0
    individualPredictions: int = 0
    gradedPredictions: int = 0
    savedParlays: int = 0
    overallAccuracy: float | None = None
    minimumGradedForAccuracy: int = 5
    scope: str = 'account'
    definitions: dict[str, str] = Field(default_factory=dict)

class FeaturedGame(CamelModel):
    game: UpcomingGame | None = None
    reason: str | None = None
    formula: dict[str, Any] = Field(default_factory=dict)
