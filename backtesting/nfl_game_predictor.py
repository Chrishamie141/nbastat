"""Deterministic, pregame-only NFL team market baseline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import erf, log, sqrt
from statistics import median, pstdev
from typing import Any

from backtesting.game_matching import normalize_team, parse_dt
from backtesting.team_history import COMPLETED_GAME_HISTORY, PREGAME_AGGREGATE


V1_MODEL_VERSION = "nfl_game_baseline_v1"
V2_MODEL_VERSION = "nfl_game_baseline_v2"
V3_MODEL_VERSION = "nfl_game_baseline_v3"
MODEL_VERSION = V1_MODEL_VERSION


def canonical_history_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Return a total, chronological ordering for an NFL history record.

    Empty or malformed timestamps sort deterministically before timestamped rows;
    eligibility checks still decide whether such records may be used.
    """
    timestamp = next((value for value in (
        parse_dt(row.get("completed_at")), parse_dt(row.get("data_as_of")),
        parse_dt(row.get("captured_at")), parse_dt(row.get("kickoff_time")),
    ) if value is not None), None)

    def integer(name: str, default: int = -1) -> int:
        try:
            return int(row.get(name, default))
        except (TypeError, ValueError):
            return default

    # Scores are final tie breakers for malformed duplicate identities. They do
    # not affect chronology, but make representative selection a total order.
    return (
        timestamp is not None, timestamp or datetime.min.replace(tzinfo=timezone.utc),
        integer("season"), integer("week"), str(row.get("game_id") or ""),
        normalize_team(row.get("team")), normalize_team(row.get("opponent")),
        str(row.get("home_away") or "").casefold(),
        str(row.get("record_role") or "").casefold(),
        str(row.get("points_for") or ""), str(row.get("points_against") or ""),
    )


def canonical_history_rows(histories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy caller-owned records and remove caller/hash insertion ordering."""
    return sorted((dict(row) for row in histories), key=canonical_history_key)


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
    margin_standard_deviation: float = 13.86
    total_standard_deviation: float = 14.5
    raw_home_win_probability: float | None = None

    @staticmethod
    def _cdf(value: float, mean: float, standard_deviation: float) -> float:
        return 0.5 * (1.0 + erf((value - mean) / (standard_deviation * sqrt(2.0))))

    def probability(self, market: str, selection: str, line: float | None = None, *, home_team: str, away_team: str) -> float:
        selection_key = selection.strip().casefold()
        normalized_selection = normalize_team(selection)
        is_home = selection_key == "home" or normalized_selection == normalize_team(home_team)
        is_away = selection_key == "away" or normalized_selection == normalize_team(away_team)
        if market == "h2h":
            if not is_home and not is_away:
                raise ValueError(f"selection is not a team in this game: {selection}")
            home_win = self.raw_home_win_probability if self.raw_home_win_probability is not None else 1.0 - self._cdf(0.0, self.expected_margin, self.margin_standard_deviation)
            return home_win if is_home else 1.0 - home_win
        if market == "spread":
            if not is_home and not is_away:
                raise ValueError(f"selection is not a team in this game: {selection}")
            selected_margin = self.expected_margin if is_home else -self.expected_margin
            return 1.0 - self._cdf(0.0, selected_margin + float(line or 0), self.margin_standard_deviation)
        if market == "total":
            if selection_key not in {"over", "under"}:
                raise ValueError(f"selection is not over/under: {selection}")
            over = 1.0 - self._cdf(float(line), self.expected_total, self.total_standard_deviation)
            return over if selection_key == "over" else 1.0 - over
        raise ValueError(f"unsupported market: {market}")

    def output(self, *, home_team: str, away_team: str, spread: float | None = None,
               total: float | None = None, market_probability: float | None = None,
               market_weight: float = 0.0) -> dict[str, Any]:
        """Return an auditable model payload; market blending is explicit and opt-in."""
        raw = self.probability("h2h", home_team, home_team=home_team, away_team=away_team)
        blended = raw if market_probability is None else (1-market_weight)*raw + market_weight*market_probability
        result = {"projected_home_points": self.home_points, "projected_away_points": self.away_points,
                  "projected_margin": self.expected_margin, "projected_total": self.expected_total,
                  "raw_home_win_probability": raw, "raw_model_probability": raw,
                  "market_implied_probability": market_probability, "blended_probability": blended,
                  "features_used": self.features, "feature_timestamps": {"data_as_of": self.data_as_of},
                  "model_version": self.model_version}
        if spread is not None:
            result["home_cover_probability"] = self.probability("spread", home_team, spread, home_team=home_team, away_team=away_team)
            result["away_cover_probability"] = 1-result["home_cover_probability"]
        if total is not None:
            result["over_probability"] = self.probability("total", "over", total, home_team=home_team, away_team=away_team)
            result["under_probability"] = 1-result["over_probability"]
        return result


class NFLGameMarketPredictorV1:
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


@dataclass(frozen=True)
class NFLGameModelConfig:
    """Documented, outcome-independent knobs for the deterministic v2 model."""

    decay: float = 0.90
    recent_games: int = 5
    recent_weight: float = 0.30
    split_prior_games: float = 6.0
    elo_k: float = 20.0
    elo_mean: float = 1500.0
    elo_regression: float = 0.33
    elo_home_advantage: float = 48.0
    elo_weight: float = 0.35
    margin_of_victory: bool = True
    market_blend_weight: float = 0.0


def no_vig_probabilities(prices: list[float]) -> list[float]:
    """Convert opposing American prices to probabilities summing exactly to one."""
    raw = []
    for price in prices:
        if price == 0:
            raise ValueError("American odds cannot be zero")
        raw.append(-price / (-price + 100) if price < 0 else 100 / (price + 100))
    total = sum(raw)
    if not raw or total <= 0:
        raise ValueError("At least one valid price is required")
    return [value / total for value in raw]


def sportsbook_consensus(rows: list[dict[str, Any]], market: str) -> dict[str, Any]:
    """Return median market context while retaining the best executable quote."""
    valid = [r for r in rows if r.get("odds") not in (None, 0, "")]
    lines = [float(r["line"]) for r in valid if r.get("line") is not None]
    probabilities = [(-float(r["odds"]) / (-float(r["odds"]) + 100) if float(r["odds"]) < 0
                      else 100 / (float(r["odds"]) + 100)) for r in valid]
    best = max(valid, key=lambda r: (float(r["odds"]), str(r.get("sportsbook") or ""))) if valid else None
    return {"market": market, "consensus_line": median(lines) if lines else None,
            "median_implied_probability": median(probabilities) if probabilities else None,
            "best_execution_quote": best}


class NFLGameMarketPredictorV2(NFLGameMarketPredictorV1):
    """Leakage-safe feature model using only completed, pre-kickoff observations."""

    def __init__(self, config: NFLGameModelConfig | None = None) -> None:
        super().__init__()
        self.config = config or NFLGameModelConfig()

    @staticmethod
    def _implied(price: float) -> float:
        return -price / (-price + 100) if price < 0 else 100 / (price + 100)

    def weighted_average(self, values: list[float]) -> float:
        """Weight oldest-to-newest values with exponential decay."""
        weights = [self.config.decay ** (len(values) - index - 1) for index in range(len(values))]
        return sum(value * weight for value, weight in zip(values, weights)) / sum(weights)

    def regress_elo(self, rating: float) -> float:
        return self.config.elo_mean + (rating - self.config.elo_mean) * (1.0 - self.config.elo_regression)

    def elo_update(self, home: float, away: float, home_score: float, away_score: float) -> tuple[float, float]:
        expected = 1 / (1 + 10 ** (-(home + self.config.elo_home_advantage - away) / 400))
        actual = 1.0 if home_score > away_score else 0.0 if home_score < away_score else 0.5
        multiplier = 1.0
        if self.config.margin_of_victory:
            multiplier = log(abs(home_score - away_score) + 1.0) * (2.2 / ((home - away) * (1 if actual == 1 else -1) * .001 + 2.2))
        change = self.config.elo_k * multiplier * (actual - expected)
        return home + change, away - change

    @staticmethod
    def rest_features(rows: list[dict[str, Any]], kickoff: Any) -> dict[str, float | bool | None]:
        start = parse_dt(kickoff)
        completed = [parse_dt(r.get("completed_at")) for r in rows]
        safe = [value for value in completed if value and start and value < start]
        days = (start - max(safe)).total_seconds() / 86400 if safe and start else None
        return {"days_since_last_game": days, "short_week": days is not None and days < 6,
                "extended_rest": days is not None and days > 10}

    def _safe_rows(self, game: dict[str, Any], histories: list[dict[str, Any]], team: str) -> list[dict[str, Any]]:
        kickoff = parse_dt(game.get("kickoff_time") or game.get("commence_time"))
        result = []
        for row in histories:
            completed = parse_dt(row.get("completed_at")); known = parse_dt(row.get("data_as_of") or row.get("captured_at"))
            if (normalize_team(row.get("team")) == team and row.get("record_role") == COMPLETED_GAME_HISTORY
                    and row.get("is_pregame") is False and completed and known and kickoff
                    and completed < kickoff and known < kickoff and known >= completed
                    and row.get("game_id") != game.get("game_id")
                    and row.get("points_for") is not None and row.get("points_against") is not None):
                result.append(row)
        return sorted(result, key=canonical_history_key)

    def _team_features(self, rows: list[dict[str, Any]], all_rows: list[dict[str, Any]], venue: str) -> dict[str, float]:
        points_for = [float(r["points_for"]) for r in rows]; points_against = [float(r["points_against"]) for r in rows]
        overall_for = self.weighted_average(points_for); overall_against = self.weighted_average(points_against)
        recent_n = min(self.config.recent_games, len(rows))
        recent_for = sum(points_for[-recent_n:]) / recent_n; recent_against = sum(points_against[-recent_n:]) / recent_n
        split = [r for r in rows if str(r.get("home_away", "")).casefold() == venue]
        n = len(split); shrink = n / (n + self.config.split_prior_games)
        split_for = sum(float(r["points_for"]) for r in split) / n if n else overall_for
        split_against = sum(float(r["points_against"]) for r in split) / n if n else overall_against
        venue_for = shrink * split_for + (1 - shrink) * overall_for
        venue_against = shrink * split_against + (1 - shrink) * overall_against
        # Opponent baselines are constructed solely from the same pre-kickoff pool.
        by_team: dict[str, list[dict[str, Any]]] = {}
        for row in all_rows: by_team.setdefault(normalize_team(row.get("team")), []).append(row)
        offensive, defensive = [], []
        for row in rows:
            opponent = by_team.get(normalize_team(row.get("opponent")), [])
            if opponent:
                offensive.append(float(row["points_for"]) - sum(float(x["points_against"]) for x in opponent) / len(opponent))
                defensive.append(sum(float(x["points_for"]) for x in opponent) / len(opponent) - float(row["points_against"]))
        return {"weighted_points_for": overall_for, "weighted_points_against": overall_against,
                "weighted_point_differential": overall_for - overall_against,
                "recent_points_for": recent_for, "recent_points_against": recent_against,
                "venue_points_for": venue_for, "venue_points_against": venue_against,
                "offensive_strength": sum(offensive) / len(offensive) if offensive else 0.0,
                "defensive_strength": sum(defensive) / len(defensive) if defensive else 0.0,
                "scoring_sd": pstdev(points_for) if len(points_for) > 1 else 0.0}

    def project(self, game: dict[str, Any], histories: list[dict[str, Any]]) -> GameProjection | None:
        histories = canonical_history_rows(histories)
        kickoff = game.get("kickoff_time") or game.get("commence_time")
        home_team, away_team = normalize_team(game.get("home_team")), normalize_team(game.get("away_team"))
        home_rows, away_rows = self._safe_rows(game, histories, home_team), self._safe_rows(game, histories, away_team)
        # V2 does not call V1.project(), so it must publish the provenance of
        # its own completed-game rows. Previously the inherited empty mapping
        # made replay aggregation report zero observations for valid forecasts.
        start = parse_dt(kickoff)
        self.last_diagnostics = {}
        for team, rows in ((home_team, home_rows), (away_team, away_rows)):
            matched = [r for r in histories if normalize_team(r.get("team")) == team]
            future = [r for r in matched if start and (
                (parse_dt(r.get("completed_at")) and parse_dt(r.get("completed_at")) >= start)
                or (parse_dt(r.get("data_as_of") or r.get("captured_at")) and
                    parse_dt(r.get("data_as_of") or r.get("captured_at")) >= start))]
            self.last_diagnostics[team] = {
                "team": team, "history_rows_available": len(matched),
                "history_rows_loaded": len(matched), "history_rows_used": len(rows),
                "minimum_required": self.MIN_GAME_HISTORY,
                "seasons_used": sorted({int(r["season"]) for r in rows if r.get("season") is not None}),
                "latest_history_timestamp": max((str(r.get("data_as_of") or r.get("completed_at")) for r in rows), default=None),
                "rejected_future_rows": len(future),
                "rejection_reasons": {"not_before_kickoff": len(future)} if future else {},
            }
        if min(len(home_rows), len(away_rows)) < self.MIN_GAME_HISTORY:
            return None
        safe_all = []
        unique_teams = {normalize_team(r.get("team")) for r in histories}
        for team in sorted(unique_teams):
            safe_all.extend(self._safe_rows(game, histories, team))
        safe_all.sort(key=canonical_history_key)
        hf = self._team_features(home_rows, safe_all, "home"); af = self._team_features(away_rows, safe_all, "away")
        recent = self.config.recent_weight
        home_off = (1-recent)*hf["venue_points_for"] + recent*hf["recent_points_for"] + hf["offensive_strength"]*.25
        away_off = (1-recent)*af["venue_points_for"] + recent*af["recent_points_for"] + af["offensive_strength"]*.25
        home_def = (1-recent)*hf["venue_points_against"] + recent*hf["recent_points_against"] - hf["defensive_strength"]*.25
        away_def = (1-recent)*af["venue_points_against"] + recent*af["recent_points_against"] - af["defensive_strength"]*.25
        home_points = (home_off + away_def)/2 + self.HOME_FIELD_POINTS/2
        away_points = (away_off + home_def)/2 - self.HOME_FIELD_POINTS/2
        ratings = {team: self.config.elo_mean for team in sorted(
            {normalize_team(r.get("team")) for r in safe_all})}
        prior_season = None
        games: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for row in safe_all:
            identity = (int(row.get("season", 0) or 0), str(row.get("game_id") or ""))
            games.setdefault(identity, []).append(row)
        # A home perspective is a canonical game observation. When only an away
        # perspective exists it is inverted below, producing the identical Elo
        # update a paired home record would have produced.
        representatives = [min(group, key=lambda row: (
            str(row.get("home_away", "")).casefold() != "home",
            canonical_history_key(row),
        )) for _, group in sorted(games.items())]
        for row in sorted(representatives, key=canonical_history_key):
            season = int(row.get("season", 0))
            if prior_season is not None and season != prior_season:
                ratings = {team: self.regress_elo(ratings[team]) for team in sorted(ratings)}
            prior_season = season
            team, opponent = normalize_team(row.get("team")), normalize_team(row.get("opponent"))
            if opponent:
                if str(row.get("home_away", "home")).casefold() == "away":
                    ratings[opponent], ratings[team] = self.elo_update(ratings.get(opponent, 1500), ratings.get(team, 1500), float(row["points_against"]), float(row["points_for"]))
                else:
                    ratings[team], ratings[opponent] = self.elo_update(ratings.get(team, 1500), ratings.get(opponent, 1500), float(row["points_for"]), float(row["points_against"]))
        home_elo, away_elo = ratings.get(home_team, 1500), ratings.get(away_team, 1500)
        elo_probability = 1/(1+10**(-(home_elo+self.config.elo_home_advantage-away_elo)/400))
        margin_probability = 1-GameProjection._cdf(0, home_points-away_points, max(8.0, sqrt(hf["scoring_sd"]**2+af["scoring_sd"]**2)))
        raw_probability = (1-self.config.elo_weight)*margin_probability+self.config.elo_weight*elo_probability
        features = {**{f"home_{k}": v for k,v in hf.items()}, **{f"away_{k}": v for k,v in af.items()},
                    "home_elo": home_elo, "away_elo": away_elo, "elo_difference": home_elo-away_elo,
                    "elo_win_probability": elo_probability, "raw_model_probability": raw_probability,
                    **{f"home_{k}": v for k,v in self.rest_features(home_rows, kickoff).items()},
                    **{f"away_{k}": v for k,v in self.rest_features(away_rows, kickoff).items()}}
        self.last_feature_diagnostics = {
            "recency_weighted_form": {k: features[k] for k in ("home_weighted_points_for", "home_weighted_points_against", "away_weighted_points_for", "away_weighted_points_against")},
            "recent_form": {k: features[k] for k in ("home_recent_points_for", "home_recent_points_against", "away_recent_points_for", "away_recent_points_against")},
            "home_away_split": {k: features[k] for k in ("home_venue_points_for", "home_venue_points_against", "away_venue_points_for", "away_venue_points_against")},
            "opponent_adjustment": {k: features[k] for k in ("home_offensive_strength", "home_defensive_strength", "away_offensive_strength", "away_defensive_strength")},
            "rest": {k: v for k, v in features.items() if "days_since_last_game" in k or "short_week" in k or "extended_rest" in k},
            "elo": {k: features[k] for k in ("home_elo", "away_elo", "elo_difference", "elo_win_probability")},
            "scoring_variance": {k: features[k] for k in ("home_scoring_sd", "away_scoring_sd")},
        }
        timestamp = max(str(r.get("data_as_of")) for r in home_rows+away_rows)
        margin_sd = max(8.0, sqrt(hf["scoring_sd"]**2+af["scoring_sd"]**2))
        total_sd = max(8.0, margin_sd)
        return GameProjection(home_points, away_points, home_points-away_points, home_points+away_points,
                              min(85.0, 55+min(len(home_rows),len(away_rows))), timestamp, features, V2_MODEL_VERSION,
                              margin_sd, total_sd, raw_probability)


class NFLGameMarketPredictor:
    """Version-selecting facade; v1 remains the compatibility default."""

    def __new__(cls, model_version: str = V1_MODEL_VERSION, config: NFLGameModelConfig | None = None):
        normalized = str(model_version).replace("-", "_")
        if normalized in {V1_MODEL_VERSION, "development"}:
            return NFLGameMarketPredictorV1()
        if normalized == V2_MODEL_VERSION:
            return NFLGameMarketPredictorV2(config)
        if normalized == V3_MODEL_VERSION:
            from .nfl_v3 import NFLGameMarketPredictorV3
            return NFLGameMarketPredictorV3(config)
        raise ValueError(f"Unsupported NFL game model version: {model_version}")
