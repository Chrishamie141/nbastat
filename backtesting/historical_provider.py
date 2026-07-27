"""Historical data provider abstraction used by replay-mode prediction runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .snapshots import SnapshotError, snapshot_path
from .team_history import COMPLETED_GAME_HISTORY, PREGAME_AGGREGATE, canonicalize_team_history


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
        path = snapshot_path(self.data_dir, league, season, week, name)
        if not path.exists():
            raise SnapshotError(
                f"No {name} snapshot found for {league.upper()} {season} Week {int(week)}:\n{path}"
            )
        data = __import__("json").loads(path.read_text())
        if not isinstance(data, list):
            raise SnapshotError(f"Malformed {name} snapshot for {league.upper()} {season} Week {int(week)}: expected a list at {path}")
        return data

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
        return [r for r in self._snapshot(league, season, week, "player_stats") if self._is_usable_history(r, season, week)]

    def get_team_stats(self, league: str, season: str, week: int) -> list[dict[str, Any]]:
        """Return team statistics available before kickoff."""
        return [canonicalize_team_history(r) for r in self._snapshot(league, season, week, "team_stats") if self._is_usable_history(r, season, week)]

    @staticmethod
    def _is_usable_history(row: dict[str, Any], season: str, week: int) -> bool:
        """Allow prior seasons and completed earlier weeks, never the replayed/future week."""
        role = row.get("record_role", PREGAME_AGGREGATE)
        if role not in {PREGAME_AGGREGATE, COMPLETED_GAME_HISTORY}:
            return False
        if role == PREGAME_AGGREGATE and not row.get("is_pregame", True):
            return False
        if role == COMPLETED_GAME_HISTORY and row.get("is_pregame") is not False:
            return False
        try:
            row_season = int(row.get("season", season))
            replay_season = int(season)
            row_week = int(row.get("week", row.get("through_week", -1)))
            return row_season < replay_season or (row_season == replay_season and row_week < int(week))
        except (TypeError, ValueError):
            return False

    def get_outcomes(self, league: str, season: str, week: int) -> list[dict[str, Any]]:
        """Return final outcomes loaded only after predictions have been frozen."""
        return self._snapshot(league, season, week, "outcomes")
