"""Historical data provider abstraction used by replay-mode prediction runs."""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
from typing import Any, Protocol

from .snapshots import SnapshotError, snapshot_path
from .team_history import (COMPLETED_GAME_HISTORY, PREGAME_AGGREGATE,
                           canonicalize_team_history, filter_game_history)
from .player_history import canonicalize_player_history, filter_player_history


class PredictionDataProvider(Protocol):
    """Provider contract consumed by prediction code in live or replay mode."""

    def get_games(self, league: str, season: str, week: int) -> list[dict[str, Any]]: ...
    def get_odds(self, league: str, season: str, week: int) -> list[dict[str, Any]]: ...
    def get_weather(self, league: str, season: str, week: int) -> list[dict[str, Any]]: ...
    def get_injuries(self, league: str, season: str, week: int) -> list[dict[str, Any]]: ...
    def get_player_stats(self, league: str, season: str, week: int) -> list[dict[str, Any]]: ...
    def get_team_stats(self, league: str, season: str, week: int) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class HistoryViews:
    """Distinct history universes sharing one authoritative game cutoff.

    V1 and the simulator consume target-team history. V2 consumes league-wide
    history for chronological Elo and opponent adjustment; V3 builds on V2 and
    therefore has the same league-wide contract. Player history is target-only.
    """
    league_team_history: Any
    target_team_history: Any
    player_history: Any


class HistoricalSnapshotProvider:
    """Load point-in-time snapshots that existed before each replayed kickoff."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._canonical_player_cache: dict[tuple[str, str], tuple[list[dict[str, Any]], dict[str, Any]]] = {}

    def canonical_player_history(self, league: str, season: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Build one indexed view across weekly snapshots, never per player/sim."""
        key=(league.lower(),str(season))
        if key in self._canonical_player_cache:
            return self._canonical_player_cache[key]
        raw=[]; games={}; base=self.data_dir/league.lower()
        # Include any available prior season and the target season. Eligibility
        # remains timestamp-driven, so scanning a future snapshot cannot leak.
        for directory in sorted(base.glob("*/week_*")):
            try: directory_season=int(directory.parent.name)
            except ValueError: continue
            if directory_season > int(season): continue
            game_path=directory/"games.json"
            if game_path.exists():
                for game in __import__("json").loads(game_path.read_text()): games[str(game.get("game_id"))]=game
            player_path=directory/"player_stats.json"
            if player_path.exists(): raw.extend(__import__("json").loads(player_path.read_text()))
        canonical, audit=canonicalize_player_history(raw,league=league,games=games)
        self._canonical_player_cache[key]=(canonical,audit)
        return canonical,audit

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
        # Stable kickoff ordering makes season and one-week replay equivalent
        # regardless of filesystem/provider response order.
        return sorted(self._snapshot(league, season, week, "games"),
                      key=lambda row: (str(row.get("kickoff_time") or ""), str(row.get("game_id") or "")))

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
        path = snapshot_path(self.data_dir, league, season, week, "player_stats")
        if not path.exists():
            return []
        return [r for r in self._snapshot(league, season, week, "player_stats") if self._is_usable_history(r, season, week)]

    def get_team_stats(self, league: str, season: str, week: int) -> list[dict[str, Any]]:
        """Return team statistics available before kickoff."""
        return [canonicalize_team_history(r) for r in self._snapshot(league, season, week, "team_stats") if self._is_usable_history(r, season, week)]

    def get_game_histories(self, league: str, season: str, week: int,
                           game: dict[str, Any]):
        """Return per-game histories and filtering diagnostics from one snapshot."""
        team_rows = [canonicalize_team_history(r) for r in self._snapshot(league, season, week, "team_stats")]
        player_rows, _audit = self.canonical_player_history(league, season)
        return HistoryViews(
            filter_game_history(game, team_rows, dataset="team", target_teams_only=False),
            filter_game_history(game, team_rows, dataset="team"),
            filter_player_history(game, player_rows),
        )

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
        """Return finals reconciled to the same week's canonical game universe."""
        from .outcomes import normalize_outcomes
        raw = self._snapshot(league, season, week, "outcomes")
        games = self.get_games(league, season, week)
        return normalize_outcomes(raw, games, league, season, week)
