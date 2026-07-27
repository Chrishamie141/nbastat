"""Deterministic, pregame-only NFL team market baseline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import erf, sqrt
from typing import Any

from backtesting.game_matching import normalize_team, parse_dt
from backtesting.team_history import COMPLETED_GAME_HISTORY, PREGAME_AGGREGATE


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
    MIN_GAME_HISTORY = 4

    def __init__(self) -> None:
        self.last_diagnostics: dict[str, Any] = {}

    def project(self, game: dict[str, Any], histories: list[dict[str, Any]]) -> GameProjection | None:
        kickoff = game.get("kickoff_time") or game.get("commence_time")
        teams = {normalize_team(game.get("home_team")), normalize_team(game.get("away_team"))}
        available = {team: 0 for team in teams}
        rejected = {team: 0 for team in teams}
        counters = {team: {name: 0 for name in (
            "history_rows_loaded", "history_rows_team_matched", "history_rows_record_role_valid",
            "history_rows_timestamp_valid", "history_rows_before_kickoff", "history_rows_scoring_fields_valid",
            "history_rows_deduplicated", "history_rows_used")} for team in teams}
        reasons = {team: {} for team in teams}
        def reject(team: str, reason: str) -> bool:
            rejected[team] += 1
            reasons[team][reason] = reasons[team].get(reason, 0) + 1
            return False
        def usable(row: dict[str, Any]) -> bool:
            team = normalize_team(row.get("team"))
            if team not in teams:
                return False
            counters[team]["history_rows_loaded"] += 1
            counters[team]["history_rows_team_matched"] += 1
            available[team] += 1
            role = row.get("record_role", PREGAME_AGGREGATE)
            if role not in {COMPLETED_GAME_HISTORY, PREGAME_AGGREGATE}:
                return reject(team, "invalid_record_role")
            if role == PREGAME_AGGREGATE and row.get("is_pregame", True) is not True:
                return reject(team, "invalid_record_role")
            if role == COMPLETED_GAME_HISTORY and row.get("is_pregame") is not False:
                return reject(team, "invalid_record_role")
            counters[team]["history_rows_record_role_valid"] += 1
            timestamp = row.get("data_as_of") or row.get("captured_at")
            known = parse_dt(timestamp)
            start = parse_dt(kickoff)
            if not start or (role == COMPLETED_GAME_HISTORY and not known):
                return reject(team, "invalid_timestamp")
            if known:
                counters[team]["history_rows_timestamp_valid"] += 1
            if known and known >= start:
                return reject(team, "not_before_kickoff")
            if role == COMPLETED_GAME_HISTORY:
                completed = row.get("completed_at")
                completed_dt = parse_dt(completed)
                if not completed_dt:
                    return reject(team, "invalid_timestamp")
                if completed_dt >= start or known < completed_dt or row.get("game_id") == game.get("game_id"):
                    return reject(team, "not_before_kickoff")
                for field in ("points_for", "points_against"):
                    try:
                        if row.get(field) is None: raise ValueError
                        float(row[field])
                    except (TypeError, ValueError):
                        return reject(team, f"missing_{field}")
                counters[team]["history_rows_scoring_fields_valid"] += 1
            counters[team]["history_rows_before_kickoff"] += 1
            try:
                kickoff_year = datetime.fromisoformat(str(kickoff).replace("Z", "+00:00")).year
                replay_season = int(game.get("season", kickoff_year))
                row_season = int(row.get("season", replay_season))
                if row_season < replay_season:
                    return True
                replay_week = int(game.get("week", 1))
                row_week = int(row.get("week", row.get("through_week", -1)))
                safe = row_season == replay_season and row_week < replay_week
                if not safe: return reject(team, "invalid_season")
                return safe
            except (TypeError, ValueError):
                return reject(team, "invalid_season")

        usable_rows = [row for row in histories if usable(row)]
        observations: dict[str, list[dict[str, Any]]] = {team: [] for team in teams}
        aggregates: dict[str, tuple[Any, dict[str, Any]]] = {}
        seen: set[tuple[Any, ...]] = set()
        for row in usable_rows:
            key = normalize_team(row.get("team"))
            if row.get("record_role") == "completed_game_history":
                identity = (row.get("season"), row.get("week"), row.get("game_id"), key)
                if identity not in seen:
                    seen.add(identity); observations[key].append(row)
                    counters[key]["history_rows_deduplicated"] += 1
                else:
                    reasons[key]["duplicate"] = reasons[key].get("duplicate", 0) + 1
            else:
                rank = (int(row.get("season", 0) or 0), int(row.get("through_week", -1) or -1), str(row.get("data_as_of") or row.get("captured_at") or ""))
                current = aggregates.get(key)
                if current is None or rank > current[0]:
                    aggregates[key] = (rank, row)
        rows: dict[str, dict[str, Any]] = {}
        for team in teams:
            games = observations[team]
            if len(games) >= self.MIN_GAME_HISTORY:
                rows[team] = {"team": team, "through_week": len(games),
                    "data_as_of": max(str(r["data_as_of"]) for r in games),
                    "stats": {"points_per_game": sum(float(r["points_for"]) for r in games)/len(games),
                              "points_allowed_per_game": sum(float(r["points_against"]) for r in games)/len(games)}}
            elif team in aggregates:
                rows[team] = aggregates[team][1]
        self.last_diagnostics = {team: {"team": team, "history_rows_available": available[team],
            "history_rows_used": len(observations[team]) if team in rows and observations[team] else int(rows.get(team, {}).get("through_week", 0) or 0),
            "minimum_required": self.MIN_GAME_HISTORY,
            "seasons_used": sorted({int(r["season"]) for r in observations[team]}) if team in rows else [],
            "latest_history_timestamp": rows.get(team, {}).get("data_as_of"),
            "rejected_future_rows": reasons[team].get("not_before_kickoff", 0),
            "rejection_reasons": dict(sorted(reasons[team].items())),
            **counters[team]} for team in sorted(teams)}
        for team in teams:
            self.last_diagnostics[team]["history_rows_used"] = len(observations[team]) if team in rows and observations[team] else int(rows.get(team, {}).get("through_week", 0) or 0)
        home = rows.get(normalize_team(game.get("home_team")))
        away = rows.get(normalize_team(game.get("away_team")))
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
