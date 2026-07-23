"""Historical data provider abstraction used by replay-mode prediction runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .utils import read_json


class PredictionDataProvider(Protocol):
    """Provider contract consumed by prediction code in live or replay mode."""

    def get_games(self, league: str, season: str, week: int) -> list[dict[str, Any]]: ...
    def get_odds(self, league: str, season: str, week: int) -> list[dict[str, Any]]: ...
    def get_weather(self, league: str, season: str, week: int) -> list[dict[str, Any]]: ...
    def get_injuries(self, league: str, season: str, week: int) -> list[dict[str, Any]]: ...
    def get_player_stats(self, league: str, season: str, week: int) -> list[dict[str, Any]]: ...
    def get_team_stats(self, league: str, season: str, week: int) -> list[dict[str, Any]]: ...


class HistoricalSnapshotProvider:
    """Load point-in-time snapshots that existed before each replayed kickoff."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def _snapshot(self, league: str, season: str, week: int, name: str) -> list[dict[str, Any]]:
        path = self.data_dir / league / str(season) / f"week_{week}" / f"{name}.json"
        return read_json(path, [])

    def get_games(self, league: str, season: str, week: int) -> list[dict[str, Any]]:
        """Return games known before kickoff for the requested historical week."""
        return self._snapshot(league, season, week, "games")

    def get_odds(self, league: str, season: str, week: int) -> list[dict[str, Any]]:
        """Return odds snapshots available before kickoff."""
        return self._snapshot(league, season, week, "odds")

    def get_weather(self, league: str, season: str, week: int) -> list[dict[str, Any]]:
        """Return weather snapshots available before kickoff."""
        return self._snapshot(league, season, week, "weather")

    def get_injuries(self, league: str, season: str, week: int) -> list[dict[str, Any]]:
        """Return injury snapshots available before kickoff."""
        return self._snapshot(league, season, week, "injuries")

    def get_player_stats(self, league: str, season: str, week: int) -> list[dict[str, Any]]:
        """Return player statistics available before kickoff."""
        return self._snapshot(league, season, week, "player_stats")

    def get_team_stats(self, league: str, season: str, week: int) -> list[dict[str, Any]]:
        """Return team statistics available before kickoff."""
        return self._snapshot(league, season, week, "team_stats")

    def get_outcomes(self, league: str, season: str, week: int) -> list[dict[str, Any]]:
        """Return final outcomes loaded only after predictions have been frozen."""
        return self._snapshot(league, season, week, "outcomes")
