"""Deterministic, pregame-only NFL team market baseline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import erf, sqrt
from typing import Any


MODEL_VERSION = "nfl-game-baseline-v1"


def _number(mapping: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = mapping.get(name)
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    return None


def _before(value: Any, kickoff: Any) -> bool:
    """Return whether an optional observation timestamp is strictly pre-kickoff."""
    if not value:
        return True
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        start = datetime.fromisoformat(str(kickoff).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return observed < start
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class GameProjection:
    home_points: float
    away_points: float
    expected_margin: float
    expected_total: float
    confidence: float
    data_as_of: str | None
    features: dict[str, float]
    model_version: str = MODEL_VERSION

    @staticmethod
    def _cdf(value: float, mean: float, standard_deviation: float) -> float:
        return 0.5 * (1.0 + erf((value - mean) / (standard_deviation * sqrt(2.0))))

    def probability(self, market: str, selection: str, line: float | None = None, *, home_team: str, away_team: str) -> float:
        selection_key = selection.casefold()
        is_home = selection_key in {"home", home_team.casefold()}
        if market == "h2h":
            home_win = 1.0 - self._cdf(0.0, self.expected_margin, 13.86)
            return home_win if is_home else 1.0 - home_win
        if market == "spread":
            selected_margin = self.expected_margin if is_home else -self.expected_margin
            return 1.0 - self._cdf(0.0, selected_margin + float(line or 0), 13.86)
        if market == "total":
            over = 1.0 - self._cdf(float(line), self.expected_total, 14.5)
            return over if selection_key == "over" else 1.0 - over
        raise ValueError(f"unsupported market: {market}")


class NFLGameMarketPredictor:
    """Project scores from prior team offense/defense without consulting odds or outcomes."""

    HOME_FIELD_POINTS = 1.5
    LEAGUE_POINTS = 22.5

    def project(self, game: dict[str, Any], histories: list[dict[str, Any]]) -> GameProjection | None:
        kickoff = game.get("kickoff_time") or game.get("commence_time")
        def usable(row: dict[str, Any]) -> bool:
            if row.get("team") not in {game.get("home_team"), game.get("away_team")} or not _before(row.get("data_as_of") or row.get("captured_at"), kickoff):
                return False
            try:
                kickoff_year = datetime.fromisoformat(str(kickoff).replace("Z", "+00:00")).year
                replay_season = int(game.get("season", kickoff_year))
                row_season = int(row.get("season", replay_season))
                if row_season < replay_season:
                    return True
                replay_week = int(game.get("week"))
                return row_season == replay_season and int(row.get("through_week", -1)) < replay_week
            except (TypeError, ValueError):
                # Providers enforce the same season/week rule. For injected providers,
                # a strict pre-kickoff data timestamp is the only safe fallback.
                return bool(row.get("data_as_of") or row.get("captured_at"))

        rows: dict[str, dict[str, Any]] = {}
        for row in histories:
            if usable(row):
                key = str(row.get("team"))
                rank = (int(row.get("season", 0) or 0), int(row.get("through_week", -1) or -1), str(row.get("data_as_of") or row.get("captured_at") or ""))
                current = rows.get(key)
                if current is None or rank > current[0]:
                    rows[key] = (rank, row)
        rows = {key: value[1] for key, value in rows.items()}
        home = rows.get(str(game.get("home_team")))
        away = rows.get(str(game.get("away_team")))
        if not home or not away:
            return None
        home_stats = home.get("stats") if isinstance(home.get("stats"), dict) else home
        away_stats = away.get("stats") if isinstance(away.get("stats"), dict) else away
        home_for = _number(home_stats, "points_per_game", "points_for_per_game", "ppg")
        away_for = _number(away_stats, "points_per_game", "points_for_per_game", "ppg")
        if home_for is None or away_for is None:
            return None
        home_against = _number(home_stats, "points_allowed_per_game", "opponent_points_per_game", "points_against_per_game")
        away_against = _number(away_stats, "points_allowed_per_game", "opponent_points_per_game", "points_against_per_game")
        # Missing defensive splits are handled by an explicit neutral league baseline,
        # never by the current game's result or its market price.
        home_points = (home_for + (away_against if away_against is not None else self.LEAGUE_POINTS)) / 2 + self.HOME_FIELD_POINTS / 2
        away_points = (away_for + (home_against if home_against is not None else self.LEAGUE_POINTS)) / 2 - self.HOME_FIELD_POINTS / 2
        through = min(int(home.get("through_week", 0) or 0), int(away.get("through_week", 0) or 0))
        confidence = min(75.0, 55.0 + min(through, 17) * 0.8 + (4.0 if home_against is not None and away_against is not None else 0.0))
        timestamps = [str(row.get("data_as_of") or row.get("captured_at")) for row in (home, away) if row.get("data_as_of") or row.get("captured_at")]
        features = {"home_points_for": home_for, "away_points_for": away_for, "home_points_against": home_against if home_against is not None else self.LEAGUE_POINTS, "away_points_against": away_against if away_against is not None else self.LEAGUE_POINTS, "home_field_points": self.HOME_FIELD_POINTS}
        return GameProjection(home_points, away_points, home_points - away_points, home_points + away_points, confidence, max(timestamps) if timestamps else None, features)
