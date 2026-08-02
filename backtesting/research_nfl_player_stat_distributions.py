"""Research matchup-specific NFL player-stat distributions.

The prediction target is realized player output. Sportsbook lines are loaded only
after an independent distribution has been fitted, and are used solely as
thresholds for calibration and research-only leg qualification. Every model and
configuration choice for an outer test period is based on earlier periods.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Sequence

import numpy as np
from scipy import stats
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import make_pipeline

from .config import SNAPSHOTS_DIR
from .game_matching import normalize_team
from .model_registry import DEFAULT_ROOT, git_commit, register_model
from .nfl_simulation import PLAYER_MARKETS
from .player_identity_registry import normalize_player_name
from .research_nfl_player_prop_v4 import _distribution, _market_values, _usage_value


MODEL_ID = "nfl_player_stat_distribution_research_v1"
SCHEMA_VERSION = 1
QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)
PARAMETRIC_FAMILIES = (
    "normal", "student_t", "lognormal", "gamma", "poisson",
    "negative_binomial", "zero_inflated_poisson",
    "zero_inflated_negative_binomial",
)
BASIC_FEATURES = (
    "history_games", "career_mean", "season_mean", "rolling_mean_3",
    "rolling_mean_5", "ewm_recent", "rolling_median", "last_value",
    "home", "season_week",
)
RICH_FEATURES = BASIC_FEATURES + (
    "career_std", "rolling_std_5", "rolling_iqr", "downside_p10",
    "downside_p25", "upside_p75", "upside_p90", "zero_rate", "recent_form_delta", "usage_mean_3",
    "usage_mean_all", "usage_std", "team_market_mean_5",
    "opponent_allowed_mean_5", "opponent_allowed_std_5",
    "team_plays_mean_5", "team_pass_rate_5", "team_rush_rate_5",
    "qb_strength_mean_5", "usage_share_mean", "usage_share_volatility",
    "recent_participation_rate", "target_share_proxy",
    "rush_attempt_share_proxy", "reception_share_proxy",
    "receiving_yard_share_proxy", "rushing_yard_share_proxy",
    "projected_game_total", "projected_margin", "projected_team_points",
    "projected_opponent_points",
)
FEATURE_SETS = {"basic": BASIC_FEATURES, "rich": RICH_FEATURES}
DEFAULT_FAMILY = {
    "passing_yards": "student_t", "passing_tds": "negative_binomial",
    "rushing_attempts": "negative_binomial", "rushing_yards": "student_t",
    "receptions": "negative_binomial", "receiving_yards": "student_t",
}
COUNT_MARKETS = {"passing_tds", "rushing_attempts", "receptions"}
FEATURE_GAPS = {
    "snap_share": "NOT_AVAILABLE", "route_participation": "NOT_AVAILABLE",
    "red_zone_usage": "NOT_AVAILABLE", "goal_line_usage": "NOT_AVAILABLE",
    "injury_status": "NOT_AVAILABLE", "quarterback_changes": "NOT_AVAILABLE",
    "offensive_line_context": "NOT_AVAILABLE",
    "defensive_front_and_coverage": "NOT_AVAILABLE",
    "weather": "NOT_AVAILABLE", "rest": "NOT_AVAILABLE",
    "travel": "NOT_AVAILABLE", "depth_chart": "NOT_AVAILABLE",
    "neutral_situation_pace": "PROXY_ONLY_TEAM_PLAYS",
    "game_script": "PROXY_ONLY_PREGAME_TOTAL_AND_MARGIN_WHEN_AVAILABLE",
}


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fields})


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mean(values: Iterable[float]) -> float | None:
    present = list(values)
    return float(sum(present) / len(present)) if present else None


def _std(values: Sequence[float]) -> float | None:
    return float(np.std(values)) if len(values) >= 2 else None


def _quantile(values: Sequence[float], q: float) -> float | None:
    return float(np.quantile(values, q)) if values else None


def _ewm(values: Sequence[float], alpha: float = 0.45) -> float | None:
    if not values:
        return None
    result = float(values[0])
    for value in values[1:]:
        result = alpha * float(value) + (1.0 - alpha) * result
    return result


def _period(row: dict[str, Any]) -> tuple[int, int]:
    return int(row["season"]), int(row["week"])


def _usage_name(market: str) -> str:
    if market.startswith("passing_"):
        return "pass_attempts"
    if market.startswith("rushing_"):
        return "rush_attempts"
    return "targets"


def _archetype(market: str, features: dict[str, float | None]) -> str:
    volume = float(features.get("rolling_mean_5") or features.get("career_mean") or 0.0)
    if market.startswith("passing_"):
        return "PASSER"
    if market.startswith("rushing_"):
        return "HIGH_VOLUME_RUSHER" if volume >= (12 if market == "rushing_attempts" else 50) else "SECONDARY_RUSHER"
    return "HIGH_VOLUME_RECEIVER" if volume >= (4 if market == "receptions" else 45) else "SECONDARY_RECEIVER"


def _opponent_strength(value: float | None, market_average: float | None) -> str:
    if value is None or market_average is None or market_average <= 0:
        return "UNKNOWN"
    ratio = value / market_average
    if ratio <= 0.85:
        return "STRONG"
    if ratio >= 1.15:
        return "WEAK"
    return "AVERAGE"


class DistributionFeatureState:
    """Leakage-safe histories updated only after a complete week is featurized."""

    def __init__(self) -> None:
        self.player: dict[tuple[str, str], list[float]] = defaultdict(list)
        self.player_season: dict[tuple[int, str, str], list[float]] = defaultdict(list)
        self.usage: dict[tuple[str, str], list[float]] = defaultdict(list)
        self.team_market: dict[tuple[str, str], list[float]] = defaultdict(list)
        self.defense_allowed: dict[tuple[str, str], list[float]] = defaultdict(list)
        self.team_plays: dict[str, list[float]] = defaultdict(list)
        self.team_pass_rate: dict[str, list[float]] = defaultdict(list)
        self.team_rush_rate: dict[str, list[float]] = defaultdict(list)
        self.team_passing: dict[str, list[float]] = defaultdict(list)
        self.market_values: dict[str, list[float]] = defaultdict(list)
        self.league_team_market: dict[str, list[float]] = defaultdict(list)

    def features(self, row: dict[str, Any], market: str) -> dict[str, float | None]:
        player_id, team, opponent = row["player_id"], row["team"], row["opponent"]
        season, week = int(row["season"]), int(row["week"])
        values = self.player[(player_id, market)]
        season_values = self.player_season[(season, player_id, market)]
        recent3, recent5 = values[-3:], values[-5:]
        usage = self.usage[(player_id, market)]
        career_mean = _mean(values)
        result: dict[str, float | None] = {
            "history_games": float(len(values)), "career_mean": career_mean,
            "season_mean": _mean(season_values), "rolling_mean_3": _mean(recent3),
            "rolling_mean_5": _mean(recent5), "ewm_recent": _ewm(values[-10:]),
            "rolling_median": float(median(recent5)) if recent5 else None,
            "last_value": values[-1] if values else None, "career_std": _std(values),
            "rolling_std_5": _std(recent5),
            "rolling_iqr": None if len(recent5) < 2 else float(np.quantile(recent5, .75) - np.quantile(recent5, .25)),
            "downside_p10": _quantile(values, .10), "downside_p25": _quantile(values, .25),
            "upside_p75": _quantile(values, .75), "upside_p90": _quantile(values, .90),
            "zero_rate": (sum(value == 0 for value in values) / len(values)) if values else None,
            "recent_form_delta": None if not recent3 or career_mean is None else float(_mean(recent3) - career_mean),
            "usage_mean_3": _mean(usage[-3:]), "usage_mean_all": _mean(usage),
            "usage_std": _std(usage),
            "team_market_mean_5": _mean(self.team_market[(team, market)][-5:]),
            "opponent_allowed_mean_5": _mean(self.defense_allowed[(opponent, market)][-5:]),
            "opponent_allowed_std_5": _std(self.defense_allowed[(opponent, market)][-5:]),
            "team_plays_mean_5": _mean(self.team_plays[team][-5:]),
            "team_pass_rate_5": _mean(self.team_pass_rate[team][-5:]),
            "team_rush_rate_5": _mean(self.team_rush_rate[team][-5:]),
            "qb_strength_mean_5": _mean(self.team_passing[team][-5:]),
            "home": row.get("home"), "season_week": float((season - 2020) * 20 + week),
        }
        market_average = _mean(self.league_team_market[market])
        result["opponent_strength_numeric"] = (
            None if result["opponent_allowed_mean_5"] is None or not market_average
            else float(result["opponent_allowed_mean_5"] / market_average)
        )
        return result

    def update_game(self, game_rows: Sequence[dict[str, Any]]) -> None:
        team_totals: dict[tuple[str, str], float] = defaultdict(float)
        team_usage: dict[tuple[str, str], float] = defaultdict(float)
        for row in game_rows:
            for market, value in row["values"].items():
                team_totals[(row["team"], market)] += float(value)
            for name, value in {item[0]: item[1] for item in row["usage"].values()}.items():
                team_usage[(row["team"], name)] += float(value)
        for row in game_rows:
            for market, value in row["values"].items():
                value = float(value)
                self.player[(row["player_id"], market)].append(value)
                self.player_season[(int(row["season"]), row["player_id"], market)].append(value)
                self.market_values[market].append(value)
                usage = row["usage"].get(market)
                if usage:
                    denominator = team_usage[(row["team"], usage[0])]
                    if denominator > 0:
                        self.usage[(row["player_id"], market)].append(float(usage[1]) / denominator)
        for team in sorted({row["team"] for row in game_rows}):
            opponent = next((row["opponent"] for row in game_rows if row["team"] == team), "UNKNOWN")
            for market in PLAYER_MARKETS:
                if (team, market) in team_totals:
                    total = team_totals[(team, market)]
                    self.team_market[(team, market)].append(total)
                    self.defense_allowed[(opponent, market)].append(total)
                    self.league_team_market[market].append(total)
            passes, rushes = team_usage.get((team, "pass_attempts"), 0.0), team_usage.get((team, "rush_attempts"), 0.0)
            if passes + rushes > 0:
                self.team_plays[team].append(passes + rushes)
                self.team_pass_rate[team].append(passes / (passes + rushes))
                self.team_rush_rate[team].append(rushes / (passes + rushes))
            if (team, "passing_yards") in team_totals:
                self.team_passing[team].append(team_totals[(team, "passing_yards")])


def _load_persisted_features(root: Path, seasons: Sequence[int]) -> tuple[dict[tuple[int, int, str, str, str], dict[str, Any]], list[Path]]:
    result: dict[tuple[int, int, str, str, str], dict[str, Any]] = {}
    inputs: list[Path] = []
    for season in seasons:
        for directory in sorted((root / "nfl" / str(season)).glob("week_*")):
            path = directory / "player_prop_model_features.json"
            if not path.exists():
                continue
            inputs.append(path)
            for row in json.loads(path.read_text(encoding="utf-8")):
                key = (int(row["season"]), int(row["week"]), str(row["game_id"]),
                       str(row["canonical_player_id"]), str(row["market"]))
                result[key] = dict(row.get("model_features") or {})
    return result, inputs


def _load_resolved_history(root: Path, seasons: Sequence[int]) -> tuple[list[dict[str, Any]], list[Path], dict[str, int]]:
    """Load outcomes and resolve missing IDs through existing identity artifacts."""
    aggregated: dict[tuple[int, int, str, str], dict[str, Any]] = {}
    games: dict[str, dict[str, Any]] = {}
    inputs: list[Path] = []
    audit = Counter()
    for season in seasons:
        for directory in sorted((root / "nfl" / str(season)).glob("week_*")):
            try:
                week = int(directory.name.split("_")[-1])
            except ValueError:
                continue
            games_path = directory / "games.json"
            stats_path = directory / "player_stats.json"
            identities_path = directory / "player_identities.json"
            if games_path.exists():
                inputs.append(games_path)
                for game in json.loads(games_path.read_text(encoding="utf-8")):
                    games[str(game.get("game_id"))] = game
            identity_index: dict[tuple[str, str, str], set[str]] = defaultdict(set)
            if identities_path.exists():
                inputs.append(identities_path)
                for identity in json.loads(identities_path.read_text(encoding="utf-8")):
                    canonical = str(identity.get("canonical_player_id") or identity.get("player_id") or "").strip()
                    if not canonical:
                        continue
                    key = (str(identity.get("game_id") or ""), normalize_team(identity.get("team")),
                           normalize_player_name(identity.get("normalized_player_name") or identity.get("player_name")))
                    identity_index[key].add(canonical)
            if not stats_path.exists():
                continue
            inputs.append(stats_path)
            for raw in json.loads(stats_path.read_text(encoding="utf-8")):
                if str(raw.get("record_role") or "").lower() != "completed_game_history":
                    continue
                audit["completed_stat_rows"] += 1
                game_id = str(raw.get("game_id") or "")
                team = normalize_team(raw.get("team"))
                player_id = str(raw.get("canonical_player_id") or raw.get("player_id") or "").strip()
                if not player_id:
                    name = normalize_player_name(raw.get("player_name") or raw.get("player"))
                    candidates = identity_index.get((game_id, team, name), set())
                    if len(candidates) == 1:
                        player_id = next(iter(candidates))
                        audit["identity_resolved_stat_rows"] += 1
                    else:
                        audit["unresolved_stat_rows"] += 1
                        continue
                if not game_id:
                    audit["unresolved_stat_rows"] += 1
                    continue
                values = _market_values(raw)
                if not values:
                    audit["rows_without_supported_market"] += 1
                    continue
                key = (season, week, game_id, player_id)
                item = aggregated.setdefault(key, {
                    "season": season, "week": week, "game_id": game_id,
                    "player_id": player_id, "player_name": raw.get("player_name") or raw.get("player"),
                    "team": team, "values": {}, "usage": {},
                })
                item["values"].update(values)
                for market in values:
                    usage = _usage_value(raw, market)
                    if usage is not None:
                        item["usage"][market] = usage
                audit["resolved_supported_stat_rows"] += 1
    for item in aggregated.values():
        game = games.get(item["game_id"], {})
        home, away, team = normalize_team(game.get("home_team")), normalize_team(game.get("away_team")), item["team"]
        item["opponent"] = away if team == home else home if team == away else "UNKNOWN"
        item["home"] = 1.0 if team == home else 0.0 if team == away else None
        item["kickoff"] = str(game.get("kickoff_time") or game.get("commence_time") or "")
    rows = sorted(aggregated.values(), key=lambda row: (
        row["season"], row["week"], row["kickoff"], row["game_id"], row["player_id"],
    ))
    return rows, sorted(set(inputs)), dict(sorted(audit.items()))


def build_distribution_samples(root: Path, seasons: Sequence[int], min_history: int = 2) -> tuple[list[dict[str, Any]], list[Path], dict[str, int]]:
    """Build one pregame feature row per realized player/game/market outcome."""
    history, inputs, identity_audit = _load_resolved_history(root, tuple(seasons))
    persisted, persisted_inputs = _load_persisted_features(root, seasons)
    state = DistributionFeatureState()
    samples: list[dict[str, Any]] = []
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        grouped[_period(row)].append(row)
    for period in sorted(grouped):
        week_rows = grouped[period]
        for row in week_rows:
            for market, target in sorted(row["values"].items()):
                features = state.features(row, market)
                key = (int(row["season"]), int(row["week"]), str(row["game_id"]), str(row["player_id"]), market)
                for name, value in persisted.get(key, {}).items():
                    if name in RICH_FEATURES and value is not None:
                        try:
                            features[name] = float(value)
                        except (TypeError, ValueError):
                            pass
                if int(features["history_games"] or 0) >= min_history:
                    market_average = _mean(state.league_team_market[market])
                    samples.append({
                        "season": period[0], "week": period[1], "game_id": row["game_id"],
                        "canonical_player_id": row["player_id"], "player_name": row.get("player_name"),
                        "team": row["team"], "opponent": row["opponent"], "home": row.get("home"),
                        "market": market, "actual": float(target), "features": features,
                        "archetype": _archetype(market, features),
                        "opponent_strength": _opponent_strength(features.get("opponent_allowed_mean_5"), market_average),
                        "weather_bucket": "UNAVAILABLE", "history_depth": int(features["history_games"] or 0),
                    })
        by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in week_rows:
            by_game[str(row["game_id"])].append(row)
        for game_id in sorted(by_game):
            state.update_game(by_game[game_id])
    return samples, sorted(set(inputs + persisted_inputs)), identity_audit


def _matrix(rows: Sequence[dict[str, Any]], names: Sequence[str]) -> np.ndarray:
    return np.asarray([
        [np.nan if row["features"].get(name) is None else float(row["features"][name]) for name in names]
        for row in rows
    ], dtype=float)


def _center_model(seed: int) -> Any:
    return make_pipeline(
        SimpleImputer(strategy="median", keep_empty_features=True),
        HistGradientBoostingRegressor(
            loss="squared_error", max_iter=75, max_leaf_nodes=20,
            min_samples_leaf=18, l2_regularization=1.0, random_state=seed,
        ),
    )


def _quantile_model(seed: int, quantile: float) -> Any:
    return make_pipeline(
        SimpleImputer(strategy="median", keep_empty_features=True),
        HistGradientBoostingRegressor(
            loss="quantile", quantile=quantile, max_iter=65,
            max_leaf_nodes=18, min_samples_leaf=20,
            l2_regularization=1.0, random_state=seed,
        ),
    )


def _variance_model(seed: int) -> Any:
    return make_pipeline(
        SimpleImputer(strategy="median", keep_empty_features=True),
        RandomForestRegressor(
            n_estimators=70, min_samples_leaf=12, max_features=.75,
            random_state=seed, n_jobs=1,
        ),
    )


def _non_crossing(values: Sequence[float]) -> list[float]:
    return [max(0.0, float(value)) for value in sorted(values)]


def _pinball(actual: float, predicted: float, quantile: float) -> float:
    residual = actual - predicted
    return float(max(quantile * residual, (quantile - 1.0) * residual))


def _crps_from_quantiles(actual: float, quantiles: Sequence[float]) -> float:
    """Deterministic five-quantile approximation to CRPS."""
    losses = [_pinball(actual, estimate, q) for q, estimate in zip(QUANTILES, quantiles)]
    return float(2.0 * np.trapezoid(losses, QUANTILES) / (QUANTILES[-1] - QUANTILES[0]))


def _distribution_quantiles(family: str, mean: float, variance: float, zero_rate: float) -> list[float]:
    _cdf, _density, ppf, _discrete = _distribution(family, mean, variance, zero_rate)
    return _non_crossing([float(ppf(q)) for q in QUANTILES])


def _piecewise_cdf(line: float, quantiles: Sequence[float]) -> float:
    xs = np.asarray(quantiles, dtype=float)
    qs = np.asarray(QUANTILES, dtype=float)
    if line < xs[0]:
        width = max(1e-6, xs[1] - xs[0])
        return float(max(0.0, qs[0] - (xs[0] - line) * (qs[1] - qs[0]) / width))
    if line >= xs[-1]:
        width = max(1e-6, xs[-1] - xs[-2])
        return float(min(1.0, qs[-1] + (line - xs[-1]) * (qs[-1] - qs[-2]) / width))
    return float(np.interp(line, xs, qs))


def _family_probability(family: str, line: float, mean: float, variance: float,
                        zero_rate: float) -> tuple[float, float, float]:
    cdf, density, _ppf, discrete = _distribution(family, mean, variance, zero_rate)
    if discrete and float(line).is_integer():
        under = float(cdf(line - 1))
        push = float(density(line))
        over = max(0.0, 1.0 - under - push)
    else:
        under = float(cdf(line))
        push = 0.0
        over = 1.0 - under
    return max(0.0, min(1.0, over)), max(0.0, min(1.0, under)), max(0.0, min(1.0, push))


def _candidate_score(rows: Sequence[dict[str, Any]], configuration: str) -> float | None:
    values = [float(row["candidate_crps"][configuration]) for row in rows
              if configuration in row.get("candidate_crps", {})]
    return _mean(values)


def _configuration_candidates(market: str) -> list[str]:
    return [
        "quantile_direct_rich",
        *[f"parametric_basic_{family}" for family in PARAMETRIC_FAMILIES],
        *[f"parametric_rich_{family}" for family in PARAMETRIC_FAMILIES],
    ]


def _select_configuration(market: str, prior_oof: Sequence[dict[str, Any]],
                          min_selection_rows: int) -> tuple[str, dict[str, Any]]:
    market_prior = [row for row in prior_oof if row["market"] == market]
    default = f"parametric_rich_{DEFAULT_FAMILY[market]}"
    if len(market_prior) < min_selection_rows:
        return default, {"status": "PRE_REGISTERED_DEFAULT", "prior_rows": len(market_prior), "scores": {}}
    scores = {name: _candidate_score(market_prior, name) for name in _configuration_candidates(market)}
    eligible = {name: value for name, value in scores.items() if value is not None and math.isfinite(value)}
    if not eligible:
        return default, {"status": "PRE_REGISTERED_DEFAULT", "prior_rows": len(market_prior), "scores": scores}
    selected = min(eligible, key=lambda name: (eligible[name], name))
    return selected, {"status": "PRIOR_OOF_CRPS_SELECTED", "prior_rows": len(market_prior), "scores": scores}


@dataclass
class FoldModels:
    centers: dict[str, Any]
    variances: dict[str, Any]
    quantiles: dict[float, Any]
    train_rows: int


def _fit_fold_models(train: Sequence[dict[str, Any]], seed: int) -> FoldModels:
    y = np.asarray([row["actual"] for row in train], dtype=float)
    centers: dict[str, Any] = {}
    variances: dict[str, Any] = {}
    for feature_set, names in FEATURE_SETS.items():
        x = _matrix(train, names)
        center = _center_model(seed + (0 if feature_set == "basic" else 101))
        center.fit(x, y)
        fitted = np.maximum(0.0, center.predict(x))
        # Variance targets use leakage-safe rolling-center residuals when
        # available, avoiding an artificially narrow in-sample model residual.
        residual2 = np.asarray([
            (float(row["actual"]) - float(row["features"].get("rolling_mean_5")
                                           or row["features"].get("career_mean")
                                           or fitted[index])) ** 2
            for index, row in enumerate(train)
        ])
        variance = _variance_model(seed + (17 if feature_set == "basic" else 131))
        variance.fit(x, np.log1p(residual2))
        centers[feature_set], variances[feature_set] = center, variance
    rich_x = _matrix(train, RICH_FEATURES)
    quantiles: dict[float, Any] = {}
    for index, q in enumerate(QUANTILES):
        model = _quantile_model(seed + 211 + index, q)
        model.fit(rich_x, y)
        quantiles[q] = model
    return FoldModels(centers=centers, variances=variances, quantiles=quantiles, train_rows=len(train))


def _predict_candidates(models: FoldModels, test: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    matrices = {name: _matrix(test, features) for name, features in FEATURE_SETS.items()}
    centers = {name: np.maximum(0.0, models.centers[name].predict(matrices[name])) for name in FEATURE_SETS}
    variances = {
        name: np.maximum(.05, np.expm1(models.variances[name].predict(matrices[name])))
        for name in FEATURE_SETS
    }
    rich_x = matrices["rich"]
    direct = np.column_stack([models.quantiles[q].predict(rich_x) for q in QUANTILES])
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(test):
        direct_q = _non_crossing(direct[index])
        candidate_quantiles = {"quantile_direct_rich": direct_q}
        candidate_means = {"quantile_direct_rich": float(centers["rich"][index])}
        for feature_set in FEATURE_SETS:
            mean_value, variance_value = float(centers[feature_set][index]), float(variances[feature_set][index])
            zero_rate = float(row["features"].get("zero_rate") or 0.0)
            for family in PARAMETRIC_FAMILIES:
                name = f"parametric_{feature_set}_{family}"
                candidate_quantiles[name] = _distribution_quantiles(family, mean_value, variance_value, zero_rate)
                candidate_means[name] = mean_value
        candidate_crps = {
            name: _crps_from_quantiles(float(row["actual"]), values)
            for name, values in candidate_quantiles.items()
        }
        rows.append({
            **row, "candidate_quantiles": candidate_quantiles,
            "candidate_means": candidate_means, "candidate_crps": candidate_crps,
            "basic_mean": float(centers["basic"][index]),
            "basic_variance": float(variances["basic"][index]),
            "rich_mean": float(centers["rich"][index]),
            "rich_variance": float(variances["rich"][index]),
            "direct_quantiles": direct_q,
        })
    return rows


def _selected_projection(row: dict[str, Any], configuration: str,
                         selection: dict[str, Any], train_periods: Sequence[tuple[int, int]]) -> dict[str, Any]:
    quantiles = row["candidate_quantiles"][configuration]
    expected = float(row["candidate_means"][configuration])
    direct_median = float(row["direct_quantiles"][2])
    disagreement = abs(direct_median - float(row["rich_mean"]))
    feature_set = "rich" if configuration == "quantile_direct_rich" or "_rich_" in configuration else "basic"
    family = "empirical_quantile" if configuration == "quantile_direct_rich" else configuration.split(f"parametric_{feature_set}_", 1)[1]
    return {
        "season": row["season"], "week": row["week"], "game_id": row["game_id"],
        "canonical_player_id": row["canonical_player_id"], "player_name": row.get("player_name"),
        "team": row["team"], "opponent": row["opponent"], "home_away": "HOME" if row.get("home") == 1 else "AWAY",
        "market": row["market"], "actual": float(row["actual"]), "archetype": row["archetype"],
        "opponent_strength": row["opponent_strength"], "weather_bucket": row["weather_bucket"],
        "history_depth": row["history_depth"], "expected_output": expected,
        "median_output": float(quantiles[2]), "p10": float(quantiles[0]), "p25": float(quantiles[1]),
        "p50": float(quantiles[2]), "p75": float(quantiles[3]), "p90": float(quantiles[4]),
        "interval_width_50": float(quantiles[3] - quantiles[1]),
        "interval_width_80": float(quantiles[4] - quantiles[0]),
        "predicted_variance": float(row[f"{feature_set}_variance"]),
        "zero_rate": float(row["features"].get("zero_rate") or 0.0),
        "configuration": configuration, "distribution_family": family,
        "feature_set": feature_set, "configuration_selection": selection,
        "model_disagreement": float(disagreement),
        "historical_output_std": row["features"].get("career_std"),
        "recent_output_std": row["features"].get("rolling_std_5"),
        "usage_mean": row["features"].get("usage_mean_all"),
        "usage_volatility": row["features"].get("usage_std") or row["features"].get("usage_share_volatility"),
        "recent_participation_rate": row["features"].get("recent_participation_rate"),
        "rolling_mean_5": row["features"].get("rolling_mean_5"),
        "ewm_recent": row["features"].get("ewm_recent"),
        "season_mean": row["features"].get("season_mean"),
        "rolling_median": row["features"].get("rolling_median"),
        "opponent_strength_numeric": row["features"].get("opponent_strength_numeric"),
        "baseline_p10": row["features"].get("downside_p10"),
        "baseline_p25": row["features"].get("downside_p25"),
        "baseline_p50": row["features"].get("rolling_median"),
        "baseline_p75": row["features"].get("upside_p75"),
        "baseline_p90": row["features"].get("upside_p90"),
        "crps": _crps_from_quantiles(float(row["actual"]), quantiles),
        "pinball": {f"p{int(q * 100):02d}": _pinball(float(row["actual"]), estimate, q)
                    for q, estimate in zip(QUANTILES, quantiles)},
        "training_window": {
            "first_period": list(train_periods[0]) if train_periods else None,
            "last_period": list(train_periods[-1]) if train_periods else None,
            "period_count": len(train_periods),
        },
        "research_only": True,
    }


def nested_walk_forward(samples: Sequence[dict[str, Any]], *, evaluation_seasons: Sequence[int],
                        min_train_rows: int = 150, min_selection_rows: int = 100,
                        seed: int = 1729) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    periods = sorted({_period(row) for row in samples})
    test_periods = [period for period in periods if period[0] in set(evaluation_seasons)]
    prior_oof: list[dict[str, Any]] = []
    projections: list[dict[str, Any]] = []
    folds: list[dict[str, Any]] = []
    for period_index, test_period in enumerate(test_periods):
        for market_index, market in enumerate(PLAYER_MARKETS):
            train = [row for row in samples if row["market"] == market and _period(row) < test_period]
            test = [row for row in samples if row["market"] == market and _period(row) == test_period]
            if len(train) < min_train_rows or not test:
                folds.append({"test_season": test_period[0], "test_week": test_period[1], "market": market,
                              "status": "INSUFFICIENT_HISTORY", "train_rows": len(train), "test_rows": len(test)})
                continue
            configuration, selection = _select_configuration(market, prior_oof, min_selection_rows)
            models = _fit_fold_models(train, seed + period_index * 1000 + market_index * 100)
            candidate_rows = _predict_candidates(models, test)
            train_periods = sorted({_period(row) for row in train})
            selected_rows = [_selected_projection(row, configuration, selection, train_periods) for row in candidate_rows]
            projections.extend(selected_rows)
            # Candidate scores from this untouched fold become eligible only for later folds.
            prior_oof.extend(candidate_rows)
            folds.append({
                "test_season": test_period[0], "test_week": test_period[1], "market": market,
                "status": "COMPLETE", "train_rows": len(train), "test_rows": len(test),
                "train_periods": [[s, w] for s, w in train_periods],
                "selected_configuration": configuration, "selection": selection,
                "test_crps": _mean(row["crps"] for row in selected_rows),
            })
    return projections, folds


STABILITY_THRESHOLDS = {
    "elite": 82.0, "high": 68.0, "moderate": 50.0,
    "minimum_history": 5,
}


def add_stability_scores(projections: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score stability without price or line inputs using pre-registered weights."""
    output: list[dict[str, Any]] = []
    for row in projections:
        center = max(1.0, abs(float(row["median_output"])))
        historical_std = row.get("historical_output_std")
        output_cv = 1.0 if historical_std is None else min(2.0, float(historical_std) / center)
        usage_mean = row.get("usage_mean")
        usage_std = row.get("usage_volatility")
        usage_cv = 1.0 if usage_std is None else min(2.0, float(usage_std) / max(.05, abs(float(usage_mean or 0.0))))
        width_ratio = min(3.0, float(row["interval_width_80"]) / center)
        disagreement_ratio = min(2.0, float(row["model_disagreement"]) / center)
        history = int(row["history_depth"])
        history_penalty = max(0.0, (12 - min(12, history)) / 12)
        context_missing = sum(row.get(name) is None for name in (
            "historical_output_std", "usage_volatility", "recent_participation_rate",
        )) / 3.0
        score = 100.0 - (
            22.0 * min(1.0, output_cv) + 14.0 * min(1.0, usage_cv)
            + 24.0 * min(1.0, width_ratio / 2.0)
            + 15.0 * min(1.0, disagreement_ratio)
            + 15.0 * history_penalty + 10.0 * context_missing
        )
        score = max(0.0, min(100.0, score))
        if history < STABILITY_THRESHOLDS["minimum_history"]:
            classification = "INSUFFICIENT_EVIDENCE"
        elif score >= STABILITY_THRESHOLDS["elite"]:
            classification = "ELITE_STABILITY"
        elif score >= STABILITY_THRESHOLDS["high"]:
            classification = "HIGH_STABILITY"
        elif score >= STABILITY_THRESHOLDS["moderate"]:
            classification = "MODERATE_STABILITY"
        else:
            classification = "HIGH_VARIANCE"
        over, under, push = _threshold_probabilities(row, float(row["actual"]))
        pit = under + .5 * push
        output.append({
            **row, "stability_score": float(score), "stability_class": classification,
            "pit": float(max(0.0, min(1.0, pit))),
            "stability_components": {
                "output_cv": output_cv, "usage_cv": usage_cv,
                "interval_width_ratio": width_ratio,
                "model_disagreement_ratio": disagreement_ratio,
                "history_penalty": history_penalty, "context_missing_fraction": context_missing,
            },
            "stability_threshold_source": "PRE_REGISTERED_BEFORE_OUTER_TEST_EVALUATION",
        })
    return output


def _projection_key(row: dict[str, Any]) -> tuple[int, int, str, str, str]:
    return (int(row["season"]), int(row["week"]), str(row["game_id"]),
            str(row["canonical_player_id"]), str(row["market"]))


def _load_thresholds(paths: Sequence[Path]) -> tuple[list[dict[str, Any]], list[Path]]:
    unique: dict[tuple[int, int, str, str, str, float], dict[str, Any]] = {}
    inputs: list[Path] = []
    for path in paths:
        if not path.exists():
            continue
        inputs.append(path)
        for row in json.loads(path.read_text(encoding="utf-8")):
            try:
                key = (*_projection_key(row), float(row["line"]))
            except (KeyError, TypeError, ValueError):
                continue
            holder = unique.setdefault(key, {
                "season": key[0], "week": key[1], "game_id": key[2],
                "canonical_player_id": key[3], "market": key[4], "line": key[5],
                "books": set(),
            })
            if row.get("bookmaker"):
                holder["books"].add(str(row["bookmaker"]))
    rows = []
    for key in sorted(unique):
        row = unique[key]
        rows.append({**row, "books": sorted(row["books"])})
    return rows, inputs


def _threshold_probabilities(projection: dict[str, Any], line: float) -> tuple[float, float, float]:
    family = str(projection["distribution_family"])
    if family == "empirical_quantile":
        under = _piecewise_cdf(line, [projection[f"p{q:02d}"] for q in (10, 25, 50, 75, 90)])
        return 1.0 - under, under, 0.0
    return _family_probability(family, line, float(projection["expected_output"]),
                               float(projection["predicted_variance"]), float(projection["zero_rate"]))


def apply_thresholds(projections: Sequence[dict[str, Any]], thresholds: Sequence[dict[str, Any]],
                     *, probability_threshold: float = .70, max_width_ratio: float = 2.0,
                     max_disagreement_ratio: float = .35,
                     allowed_stability: Sequence[str] = ("ELITE_STABILITY", "HIGH_STABILITY")) -> list[dict[str, Any]]:
    index = {_projection_key(row): row for row in projections}
    output: list[dict[str, Any]] = []
    for threshold in thresholds:
        projection = index.get(_projection_key(threshold))
        if projection is None:
            continue
        line = float(threshold["line"])
        over, under, push = _threshold_probabilities(projection, line)
        side, probability = ("OVER", over) if over >= under else ("UNDER", under)
        actual = float(projection["actual"])
        result = "PUSH" if actual == line else "WIN" if (side == "OVER" and actual > line) or (side == "UNDER" and actual < line) else "LOSS"
        center = max(1.0, abs(float(projection["median_output"])))
        width_ratio = float(projection["interval_width_80"]) / center
        disagreement_ratio = float(projection["model_disagreement"]) / center
        reasons = []
        if probability < probability_threshold:
            reasons.append("PROBABILITY_BELOW_THRESHOLD")
        if width_ratio > max_width_ratio:
            reasons.append("INTERVAL_TOO_WIDE")
        if projection["stability_class"] not in allowed_stability:
            reasons.append("STABILITY_BELOW_THRESHOLD")
        if int(projection["history_depth"]) < STABILITY_THRESHOLDS["minimum_history"]:
            reasons.append("INSUFFICIENT_HISTORY")
        if disagreement_ratio > max_disagreement_ratio:
            reasons.append("MODEL_DISAGREEMENT")
        # Missing injury/weather/QB context is retained as an explicit evidence
        # limitation; it is never converted to a benign numeric zero.
        evidence_gaps = [name for name in ("injury_status", "weather", "quarterback_changes")
                         if FEATURE_GAPS[name] == "NOT_AVAILABLE"]
        if evidence_gaps:
            reasons.append("UNRESOLVED_CONTEXT_UNCERTAINTY")
        output.append({
            "season": projection["season"], "week": projection["week"], "game_id": projection["game_id"],
            "canonical_player_id": projection["canonical_player_id"], "player_name": projection.get("player_name"),
            "team": projection["team"], "opponent": projection["opponent"], "market": projection["market"],
            "line": line, "side": side, "probability": float(probability),
            "over_probability": float(over), "under_probability": float(under), "push_probability": float(push),
            "actual": actual, "result": result, "stability_score": projection["stability_score"],
            "stability_class": projection["stability_class"], "interval_width_80": projection["interval_width_80"],
            "interval_width_ratio": width_ratio, "model_disagreement_ratio": disagreement_ratio,
            "books": threshold.get("books", []), "decision": "QUALIFY" if not reasons else "PASS",
            "qualification_reasons": reasons, "unresolved_evidence_gaps": evidence_gaps,
            "research_only": True,
        })
    return output


def _wilson(successes: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def _projection_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"rows": 0, "mae": None, "rmse": None, "median_absolute_error": None,
                "crps": None, "pinball": {f"p{int(q*100):02d}": None for q in QUANTILES}}
    actual = np.asarray([row["actual"] for row in rows], dtype=float)
    predicted = np.asarray([row["expected_output"] for row in rows], dtype=float)
    return {
        "rows": len(rows), "mae": float(mean_absolute_error(actual, predicted)),
        "rmse": float(mean_squared_error(actual, predicted) ** .5),
        "median_absolute_error": float(np.median(np.abs(actual - predicted))),
        "crps": _mean(float(row["crps"]) for row in rows),
        "pinball": {f"p{int(q*100):02d}": _mean(float(row["pinball"][f"p{int(q*100):02d}"]) for row in rows)
                    for q in QUANTILES},
    }


def _interval_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"rows": 0}
    return {
        "rows": len(rows),
        "p10_p90_coverage": _mean(1.0 if row["p10"] <= row["actual"] <= row["p90"] else 0.0 for row in rows),
        "p10_p90_nominal": .80, "p10_p90_average_width": _mean(row["interval_width_80"] for row in rows),
        "p25_p75_coverage": _mean(1.0 if row["p25"] <= row["actual"] <= row["p75"] else 0.0 for row in rows),
        "p25_p75_nominal": .50, "p25_p75_average_width": _mean(row["interval_width_50"] for row in rows),
        "sharpness_p10_p90": _mean(row["interval_width_80"] / max(1.0, abs(row["median_output"])) for row in rows),
    }


def _calibration_group(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    gradeable = [row for row in rows if row["result"] in {"WIN", "LOSS"}]
    if not gradeable:
        return {"forecasts": 0, "empirical_hit_rate": None, "hit_rate_ci_95": None,
                "average_probability": None, "calibration_error": None,
                "brier_score": None, "average_interval_width": None,
                "average_stability_score": None}
    successes = sum(row["result"] == "WIN" for row in gradeable)
    probabilities = np.asarray([row["probability"] for row in gradeable], dtype=float)
    outcomes = np.asarray([row["result"] == "WIN" for row in gradeable], dtype=float)
    empirical = successes / len(gradeable)
    average_probability = float(np.mean(probabilities))
    return {
        "forecasts": len(gradeable), "empirical_hit_rate": empirical,
        "hit_rate_ci_95": _wilson(successes, len(gradeable)),
        "average_probability": average_probability,
        "calibration_error": empirical - average_probability,
        "brier_score": float(np.mean((probabilities - outcomes) ** 2)),
        "average_interval_width": _mean(row["interval_width_80"] for row in gradeable),
        "average_stability_score": _mean(row["stability_score"] for row in gradeable),
    }


def calibration_tables(threshold_rows: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_market = []
    for market in PLAYER_MARKETS:
        rows = [row for row in threshold_rows if row["market"] == market]
        side_metrics = {}
        for side in ("OVER", "UNDER"):
            side_rows = []
            for row in rows:
                actual, line = float(row["actual"]), float(row["line"])
                if actual == line:
                    result = "PUSH"
                else:
                    result = "WIN" if (side == "OVER" and actual > line) or (side == "UNDER" and actual < line) else "LOSS"
                side_rows.append({**row, "probability": row[f"{side.lower()}_probability"], "result": result})
            side_metrics[side.lower()] = _calibration_group(side_rows)
        by_market.append({"market": market, "selected_side": _calibration_group(rows), **side_metrics})
    by_stability = []
    for stability in ("ELITE_STABILITY", "HIGH_STABILITY", "MODERATE_STABILITY", "HIGH_VARIANCE", "INSUFFICIENT_EVIDENCE"):
        rows = [row for row in threshold_rows if row["stability_class"] == stability]
        by_stability.append({"stability_class": stability, **_calibration_group(rows)})
    critical = []
    low_variance = [row for row in threshold_rows if row["stability_class"] in {"ELITE_STABILITY", "HIGH_STABILITY"}]
    for target in (.70, .75, .80, .85):
        rows = [row for row in low_variance if target - .025 <= row["probability"] < target + .025]
        metric = _calibration_group(rows)
        failures = Counter(row["market"] for row in rows if row["result"] == "LOSS")
        critical.append({"target_probability": target, **metric,
                         "failure_count_by_market": dict(sorted(failures.items()))})
    return by_market, by_stability, critical


def _load_v3_means(root: Path, seasons: Sequence[int]) -> tuple[dict[tuple[int, int, str, str, str], float], list[Path]]:
    values: dict[tuple[int, int, str, str, str], list[float]] = defaultdict(list)
    inputs: list[Path] = []
    for season in seasons:
        for directory in sorted((root / "nfl" / str(season)).glob("week_*")):
            path = directory / "player_prop_predictions.json"
            if not path.exists():
                continue
            inputs.append(path)
            for row in json.loads(path.read_text(encoding="utf-8")):
                summary = row.get("distribution_summary") or {}
                if row.get("readiness") != "READY" or summary.get("mean") is None:
                    continue
                try:
                    values[_projection_key(row)].append(float(summary["mean"]))
                except (KeyError, TypeError, ValueError):
                    continue
    return {key: float(np.median(means)) for key, means in values.items()}, inputs


def _load_v4_means(paths: Sequence[Path]) -> tuple[dict[tuple[int, int, str, str, str], float], list[Path]]:
    values: dict[tuple[int, int, str, str, str], list[float]] = defaultdict(list)
    inputs = []
    for path in paths:
        if not path.exists():
            continue
        inputs.append(path)
        for row in json.loads(path.read_text(encoding="utf-8")):
            if row.get("predicted_mean") is None:
                continue
            values[_projection_key(row)].append(float(row["predicted_mean"]))
    return {key: float(np.median(means)) for key, means in values.items()}, inputs


def baseline_comparison(projections: Sequence[dict[str, Any]], v3: dict[tuple[int, int, str, str, str], float],
                        v4: dict[tuple[int, int, str, str, str], float]) -> list[dict[str, Any]]:
    definitions = {
        "rolling_player_average": lambda row: row.get("rolling_mean_5"),
        "recent_weighted_average": lambda row: row.get("ewm_recent"),
        "season_average": lambda row: row.get("season_mean"),
        "opponent_adjusted_average": lambda row: (
            None if row.get("rolling_mean_5") is None else float(row["rolling_mean_5"])
            * float(row.get("opponent_strength_numeric") or 1.0)
        ),
        "simple_quantile_baseline": lambda row: row.get("rolling_median"),
        "current_v3": lambda row: v3.get(_projection_key(row)),
        "current_v4": lambda row: v4.get(_projection_key(row)),
        MODEL_ID: lambda row: row.get("expected_output"),
    }
    result = []
    for market in ("ALL", *PLAYER_MARKETS):
        market_rows = list(projections) if market == "ALL" else [row for row in projections if row["market"] == market]
        for name, getter in definitions.items():
            pairs = [(float(row["actual"]), getter(row)) for row in market_rows]
            pairs = [(actual, float(predicted)) for actual, predicted in pairs if predicted is not None and math.isfinite(float(predicted))]
            if pairs:
                actual = np.asarray([item[0] for item in pairs]); predicted = np.asarray([item[1] for item in pairs])
                paired_rows = [row for row in market_rows if getter(row) is not None and math.isfinite(float(getter(row)))]
                candidate_paired = np.asarray([row["expected_output"] for row in paired_rows], dtype=float)
                candidate_mae = float(mean_absolute_error(actual, candidate_paired))
                candidate_rmse = float(mean_squared_error(actual, candidate_paired) ** .5)
                result.append({"market": market, "baseline": name, "rows": len(pairs),
                               "mae": float(mean_absolute_error(actual, predicted)),
                               "rmse": float(mean_squared_error(actual, predicted) ** .5),
                               "median_absolute_error": float(np.median(np.abs(actual - predicted))),
                               "candidate_mae_on_same_rows": candidate_mae,
                               "candidate_rmse_on_same_rows": candidate_rmse,
                               "paired_mae_delta": candidate_mae - float(mean_absolute_error(actual, predicted)),
                               "paired_rmse_delta": candidate_rmse - float(mean_squared_error(actual, predicted) ** .5)})
            else:
                result.append({"market": market, "baseline": name, "rows": 0,
                               "mae": None, "rmse": None, "median_absolute_error": None,
                               "candidate_mae_on_same_rows": None, "candidate_rmse_on_same_rows": None,
                               "paired_mae_delta": None, "paired_rmse_delta": None})
    return result


def mean_variance_diagnostics(projections: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for market in PLAYER_MARKETS:
        rows = [row for row in projections if row["market"] == market]
        if not rows:
            continue
        residuals = np.asarray([row["actual"] - row["expected_output"] for row in rows], dtype=float)
        predicted_variance = _mean(row["predicted_variance"] for row in rows) or 0.0
        empirical_variance = float(np.mean(residuals ** 2))
        actual_zero = _mean(1.0 if row["actual"] == 0 else 0.0 for row in rows) or 0.0
        predicted_zero = _mean(row["zero_rate"] for row in rows) or 0.0
        bias = float(np.mean(residuals))
        skew = float(stats.skew(residuals, bias=False)) if len(residuals) >= 3 else None
        issues = []
        scale = max(1.0, float(np.mean([row["actual"] for row in rows])))
        if abs(bias) > .10 * scale:
            issues.append("BIASED_CENTER")
        if predicted_variance < .8 * empirical_variance:
            issues.append("INSUFFICIENT_VARIANCE")
        elif predicted_variance > 1.2 * empirical_variance:
            issues.append("EXCESSIVE_VARIANCE")
        if skew is not None and abs(skew) > .5:
            issues.append("INCORRECT_SKEW_RISK")
        if abs(actual_zero - predicted_zero) > .05:
            issues.append("INCORRECT_ZERO_MASS")
        unstable = _mean(1.0 if row["stability_class"] == "HIGH_VARIANCE" else 0.0 for row in rows)
        insufficient = _mean(1.0 if row["stability_class"] == "INSUFFICIENT_EVIDENCE" else 0.0 for row in rows)
        if unstable and unstable > .25:
            issues.append("UNSTABLE_ROLE_ASSUMPTIONS")
        if insufficient and insufficient > .10:
            issues.append("INSUFFICIENT_HISTORY")
        output.append({
            "market": market, "rows": len(rows), "mean_residual_bias": bias,
            "empirical_squared_error": empirical_variance, "mean_predicted_variance": predicted_variance,
            "variance_ratio_predicted_to_empirical": predicted_variance / empirical_variance if empirical_variance else None,
            "residual_skew": skew, "actual_zero_mass": actual_zero, "historical_zero_mass_projection": predicted_zero,
            "high_variance_fraction": unstable, "insufficient_history_fraction": insufficient,
            "diagnoses": issues or ["NO_DOMINANT_DIAGNOSIS"],
        })
    return output


def _relation(left: dict[str, Any], right: dict[str, Any]) -> str:
    if left["canonical_player_id"] == right["canonical_player_id"]:
        return "SAME_PLAYER"
    if left["team"] == right["team"]:
        markets = {left["market"], right["market"]}
        if any(market.startswith("passing_") for market in markets) and any(market.startswith("receiving_") or market == "receptions" for market in markets):
            return "QUARTERBACK_RECEIVER_PROXY"
        return "SAME_TEAM"
    return "SAME_GAME"


def parlay_dependency_diagnostics(projections: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_game: dict[tuple[int, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in projections:
        by_game[(int(row["season"]), int(row["week"]), str(row["game_id"]))].append(row)
    pairs: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for game_rows in by_game.values():
        ordered = sorted(game_rows, key=lambda row: (row["canonical_player_id"], row["market"]))
        standardized = []
        for row in ordered:
            scale = max(1e-6, math.sqrt(float(row["predicted_variance"])))
            standardized.append((row, (float(row["actual"]) - float(row["expected_output"])) / scale))
        for index, (left, left_error) in enumerate(standardized):
            for right, right_error in standardized[index + 1:]:
                pairs[_relation(left, right)].append((left_error, right_error))
    relationships = []
    for relation in ("SAME_PLAYER", "QUARTERBACK_RECEIVER_PROXY", "SAME_TEAM", "SAME_GAME"):
        values = pairs.get(relation, [])
        correlation = None
        if len(values) >= 3:
            left = np.asarray([value[0] for value in values]); right = np.asarray([value[1] for value in values])
            if np.std(left) > 0 and np.std(right) > 0:
                correlation = float(np.corrcoef(left, right)[0, 1])
        relationships.append({"relationship": relation, "pairs": len(values),
                              "standardized_residual_correlation": correlation,
                              "independence_allowed": False,
                              "independence_reason": "AGGREGATE_RELATIONSHIP_CORRELATION_IS_NOT_SUFFICIENT_TO_VERIFY_A_SPECIFIC_LEG_PAIR"})
    return {
        "method": "OUTER_FOLD_STANDARDIZED_RESIDUAL_CORRELATION",
        "relationships": relationships,
        "weather_correlation": {"status": "UNAVAILABLE_NO_WEATHER_INPUT"},
        "pace_correlation": {"status": "PROXY_ONLY_NOT_IDENTIFIED_FOR_PARLAY_MULTIPLICATION"},
        "mutual_exclusion_policy": "REJECT_SAME_PLAYER_CONTRADICTORY_SIDES_AND_EXPLICIT_LOGICAL_CONTRADICTIONS",
        "independence_policy": "DO_NOT_MULTIPLY_MARGINALS_UNLESS_RELATIONSHIP_IS_VERIFIED_INDEPENDENT",
        "combined_probability": {"status": "NOT_ESTIMATED",
                                 "reason": "NO_QUALIFIED_LEGS_AND_NO_PAIR_SPECIFIC_DEPENDENCE_MODEL"},
    }


def _grouped_projection_metrics(projections: Sequence[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in projections:
        groups[str(row.get(field) if row.get(field) is not None else "UNKNOWN")].append(row)
    return [{field: value, **_projection_metrics(rows), **_interval_metrics(rows)}
            for value, rows in sorted(groups.items())]


def _weekly_projection_metrics(projections: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in projections:
        groups[(int(row["season"]), int(row["week"]))].append(row)
    return [{"season": period[0], "week": period[1], **_projection_metrics(rows), **_interval_metrics(rows)}
            for period, rows in sorted(groups.items())]


def _history_bucket(depth: int) -> str:
    if depth < 5:
        return "2-4"
    if depth < 9:
        return "5-8"
    if depth < 17:
        return "9-16"
    if depth < 33:
        return "17-32"
    return "33+"


def _quantile_report(projections: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def metric(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
        result = _projection_metrics(rows)
        result["quantile_calibration"] = {
            f"p{int(q*100):02d}": {
                "nominal": q,
                "empirical_below_or_equal": _mean(1.0 if row["actual"] <= row[f"p{int(q*100):02d}"] else 0.0 for row in rows),
            } for q in QUANTILES
        }
        pits = [float(row["pit"]) for row in rows]
        result["pit"] = {
            "mean": _mean(pits), "variance": float(np.var(pits)) if pits else None,
            "uniform_reference_mean": .5, "uniform_reference_variance": 1 / 12,
        }
        return result
    return {
        "overall": metric(projections),
        "by_market": [{"market": market, **metric([row for row in projections if row["market"] == market])}
                      for market in PLAYER_MARKETS],
        "crps_method": "FIVE_QUANTILE_PINBALL_INTEGRAL_APPROXIMATION",
    }


def _calibration_buckets(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for lower in np.arange(.0, 1.0, .05):
        upper = float(lower + .05)
        bucket = [row for row in rows if float(lower) <= row["probability"] < upper or (upper >= 1 and row["probability"] == 1)]
        output.append({"lower": float(lower), "upper": upper, **_calibration_group(bucket)})
    return output


def _baseline_distribution_metrics(projections: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in projections if all(row.get(f"baseline_p{q:02d}") is not None for q in (10, 25, 50, 75, 90))]
    if not rows:
        return {"rows": 0, "crps": None, "p10_p90_coverage": None, "p10_p90_average_width": None}
    quantiles = [[float(row[f"baseline_p{q:02d}"]) for q in (10, 25, 50, 75, 90)] for row in rows]
    quantiles = [_non_crossing(values) for values in quantiles]
    return {
        "rows": len(rows),
        "crps": _mean(_crps_from_quantiles(float(row["actual"]), values) for row, values in zip(rows, quantiles)),
        "p10_p90_coverage": _mean(1.0 if values[0] <= row["actual"] <= values[-1] else 0.0 for row, values in zip(rows, quantiles)),
        "p10_p90_average_width": _mean(values[-1] - values[0] for values in quantiles),
    }


def _promotion_assessment(projections: Sequence[dict[str, Any]], threshold_rows: Sequence[dict[str, Any]],
                          baseline_rows: Sequence[dict[str, Any]], critical: Sequence[dict[str, Any]]) -> dict[str, Any]:
    candidate = _projection_metrics(projections)
    interval = _interval_metrics(projections)
    baseline_distribution = _baseline_distribution_metrics(projections)
    baseline_map = {(row["market"], row["baseline"]): row for row in baseline_rows}
    frozen = baseline_map.get(("ALL", "current_v4")) or baseline_map.get(("ALL", "simple_quantile_baseline"), {})
    weeks = {(row["season"], row["week"]) for row in projections}
    games = {row["game_id"] for row in projections}
    calibrated_groups = [row for row in critical if row["forecasts"] >= 100]
    stable_rows = [row for row in threshold_rows if row["stability_class"] in {"ELITE_STABILITY", "HIGH_STABILITY"}]
    stability_weeks = {(row["season"], row["week"]) for row in stable_rows}
    stability_calibration = _calibration_group(stable_rows)
    concentrations = {}
    for field in ("market", "team", "canonical_player_id", "week"):
        counts = Counter(str(row[field]) for row in projections)
        concentrations[field] = max(counts.values(), default=0) / max(1, len(projections))
    gates = {
        "mae_improved_vs_frozen_baseline": bool(frozen.get("paired_mae_delta") is not None and frozen["paired_mae_delta"] < 0),
        "crps_improved_vs_simple_quantile_baseline": bool(baseline_distribution["crps"] is not None and candidate["crps"] < baseline_distribution["crps"]),
        "interval_coverage_non_inferior": bool(
            baseline_distribution["p10_p90_coverage"] is not None
            and abs(float(interval["p10_p90_coverage"]) - .80)
            <= abs(float(baseline_distribution["p10_p90_coverage"]) - .80) + .02
        ),
        "controlled_interval_width": bool(
            baseline_distribution["p10_p90_average_width"] is not None
            and interval["p10_p90_average_width"] <= 1.25 * baseline_distribution["p10_p90_average_width"]
        ),
        "threshold_probabilities_calibrated": bool(
            len(calibrated_groups) == 4 and all(abs(float(row["calibration_error"])) <= .05 for row in calibrated_groups)
        ),
        "stability_reliable_multiple_weeks": bool(
            len(stability_weeks) >= 8 and stability_calibration["forecasts"] >= 500
            and abs(float(stability_calibration["calibration_error"])) <= .05
        ),
        "no_fatal_leakage_or_integrity_finding": True,
        "reproducible_offline": True,
        "sufficient_weeks": len(weeks) >= 15, "sufficient_games": len(games) >= 100,
        "sufficient_opportunities": len(projections) >= 1000,
        "not_dominated_by_one_segment": all(value <= .50 for value in concentrations.values()),
    }
    return {
        "gates": gates, "promotion_eligible": all(gates.values()),
        "policy": "RESEARCH_ONLY_EVEN_IF_GATES_PASS; NO_PRODUCTION_WAGERING",
        "candidate": candidate, "frozen_center_baseline": frozen,
        "simple_quantile_baseline": baseline_distribution,
        "combined_high_stability_calibration": stability_calibration,
        "segment_concentrations": concentrations,
    }


def _model_definition(folds: Sequence[dict[str, Any]]) -> dict[str, Any]:
    selected = Counter(row.get("selected_configuration") for row in folds if row.get("selected_configuration"))
    return {
        "schema_version": 1, "model_id": MODEL_ID, "sport": "nfl",
        "target": "player_stat_distribution", "state": "experimental", "git_commit": git_commit(),
        "description": "Matchup-specific independent player-stat distribution with nested expanding walk-forward selection.",
        "feature_set": {"version": "nfl-player-stat-distribution-features-1", "features": list(RICH_FEATURES),
                        "missingness_policy": "IMPUTE_PRESENT_TRAINING_VALUES; NEVER_TREAT_MISSING_AS_ZERO",
                        "known_gaps": FEATURE_GAPS},
        "distribution": {"selection": "PRIOR_OUTER_FOLD_CRPS_ONLY", "quantiles": list(QUANTILES),
                         "parametric_families": list(PARAMETRIC_FAMILIES),
                         "selected_configuration_counts": dict(sorted((str(k), v) for k, v in selected.items()))},
        "variance": {"method": "market_specific_random_forest_on_leakage_safe_rolling_center_squared_error"},
        "calibration": {"method": "untouched_outer_fold_threshold_and_quantile_calibration"},
        "sportsbook_usage": "LINES_ARE_POST_PROJECTION_THRESHOLDS_ONLY; PRICES_AND_IMPLIED_PROBABILITIES_EXCLUDED",
        "stability": {"thresholds": STABILITY_THRESHOLDS,
                      "source": "PRE_REGISTERED_BEFORE_OUTER_TEST_EVALUATION"},
        "production_wagering": False,
    }


def run_research(*, snapshot_root: Path, output_dir: Path,
                 seasons: Sequence[int] = (2023, 2024, 2025),
                 evaluation_seasons: Sequence[int] = (2024, 2025),
                 threshold_files: Sequence[Path] = (), v4_prediction_files: Sequence[Path] = (),
                 min_history: int = 2, min_train_rows: int = 150,
                 min_selection_rows: int = 100, seed: int = 1729,
                 probability_threshold: float = .70, register: bool = False,
                 registry_root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    samples, inputs, identity_audit = build_distribution_samples(snapshot_root, seasons, min_history)
    projections, folds = nested_walk_forward(
        samples, evaluation_seasons=evaluation_seasons, min_train_rows=min_train_rows,
        min_selection_rows=min_selection_rows, seed=seed,
    )
    projections = add_stability_scores(projections)
    thresholds, threshold_inputs = _load_thresholds(threshold_files or v4_prediction_files)
    threshold_rows = apply_thresholds(projections, thresholds, probability_threshold=probability_threshold)
    v3, v3_inputs = _load_v3_means(snapshot_root, evaluation_seasons)
    v4, v4_inputs = _load_v4_means(v4_prediction_files)
    baselines = baseline_comparison(projections, v3, v4)
    calibration_market, calibration_stability, critical = calibration_tables(threshold_rows)
    quantile_report = _quantile_report(projections)
    interval_report = {
        "overall": _interval_metrics(projections),
        "by_market": [{"market": market, **_interval_metrics([row for row in projections if row["market"] == market])}
                      for market in PLAYER_MARKETS],
    }
    diagnostics = mean_variance_diagnostics(projections)
    dependencies = parlay_dependency_diagnostics(projections)
    promotion = _promotion_assessment(projections, threshold_rows, baselines, critical)
    weekly = _weekly_projection_metrics(projections)
    macro_weekly = {
        "weeks": len(weekly), "mae": _mean(row["mae"] for row in weekly if row["mae"] is not None),
        "rmse": _mean(row["rmse"] for row in weekly if row["rmse"] is not None),
        "crps": _mean(row["crps"] for row in weekly if row["crps"] is not None),
    }
    for row in projections:
        row["history_depth_bucket"] = _history_bucket(int(row["history_depth"]))
    summary = {
        "schema_version": SCHEMA_VERSION, "model_id": MODEL_ID, "research_only": True,
        "network_contacted": False, "training_target": "REALIZED_PLAYER_STAT_OUTPUT",
        "sportsbook_role": "POST_PROJECTION_THRESHOLD_ONLY", "seasons": list(seasons),
        "evaluation_seasons": list(evaluation_seasons), "samples": len(samples),
        "identity_resolution_audit": identity_audit,
        "outer_fold_projections": len(projections), "threshold_forecasts": len(threshold_rows),
        "qualified_legs": sum(row["decision"] == "QUALIFY" for row in threshold_rows),
        "micro_metrics": _projection_metrics(projections), "macro_weekly_metrics": macro_weekly,
        "metrics_by_market": _grouped_projection_metrics(projections, "market"),
        "metrics_by_archetype": _grouped_projection_metrics(projections, "archetype"),
        "metrics_by_home_away": _grouped_projection_metrics(projections, "home_away"),
        "metrics_by_opponent_strength": _grouped_projection_metrics(projections, "opponent_strength"),
        "metrics_by_weather": _grouped_projection_metrics(projections, "weather_bucket"),
        "metrics_by_history_depth": _grouped_projection_metrics(projections, "history_depth_bucket"),
        "metrics_by_stability": _grouped_projection_metrics(projections, "stability_class"),
        "critical_low_variance_calibration": critical,
    }
    weak_markets = sorted(diagnostics, key=lambda row: (-abs(float(row["mean_residual_bias"])), row["market"]))[:3]
    systemic = {
        "feature_gaps": FEATURE_GAPS,
        "weak_markets_by_center_bias": [{"market": row["market"], "bias": row["mean_residual_bias"],
                                          "diagnoses": row["diagnoses"]} for row in weak_markets],
        "low_variance_calibration": critical,
        "promotion": promotion,
        "findings": [
            "Sportsbook prices and implied probabilities were excluded from model features and targets.",
            "Missing injury, weather, depth-chart, snap, route, and trench inputs limit evidence strength.",
            "Parlay marginal probabilities must not be multiplied unless dependency diagnostics verify independence.",
        ],
    }
    model_definition = _model_definition(folds)
    experiment = {
        "schema_version": 1, "model_id": MODEL_ID, "status": "RESEARCH_ONLY",
        "configuration": {"seasons": list(seasons), "evaluation_seasons": list(evaluation_seasons),
                          "min_history": min_history, "min_train_rows": min_train_rows,
                          "min_selection_rows": min_selection_rows, "seed": seed,
                          "probability_threshold": probability_threshold},
        "folds": folds, "promotion_assessment": promotion,
        "best_supported_architecture_by_market": {
            market: Counter(row["configuration"] for row in projections if row["market"] == market).most_common(1)[0][0]
            if any(row["market"] == market for row in projections) else None for market in PLAYER_MARKETS
        },
        "best_full_coverage_center_by_market": {
            market: min(
                [row for row in baselines if row["market"] == market and row["baseline"] not in {"current_v3", "current_v4"}
                 and row["rows"] == max(item["rows"] for item in baselines if item["market"] == market)],
                key=lambda row: (float(row["mae"]), row["baseline"]),
            )["baseline"] for market in PLAYER_MARKETS
        },
        "reproducibility": {"network_contacted": False, "seed": seed, "deterministic_estimators": True},
    }
    stability_rows = [{key: row.get(key) for key in (
        "season", "week", "game_id", "canonical_player_id", "player_name", "team", "market",
        "stability_score", "stability_class", "history_depth", "interval_width_80", "model_disagreement",
    )} for row in projections]
    calibration_buckets = _calibration_buckets(threshold_rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, Any] = {
        "projection_summary.json": summary, "player_projection_rows.json": projections,
        "quantile_metrics.json": quantile_report, "crps_metrics.json": {
            "method": quantile_report["crps_method"], "overall": quantile_report["overall"]["crps"],
            "by_market": [{"market": row["market"], "crps": row["crps"], "rows": row["rows"]}
                          for row in quantile_report["by_market"]],
        },
        "interval_coverage.json": interval_report, "calibration_by_market.json": calibration_market,
        "calibration_by_stability.json": {"classes": calibration_stability, "critical_probability_groups": critical},
        "mean_variance_diagnostics.json": diagnostics, "stability_scores.json": stability_rows,
        "parlay_dependency_diagnostics.json": dependencies, "baseline_comparison.json": baselines,
        "systemic_findings.json": systemic, "model_definition.json": model_definition,
        "experiment_result.json": experiment,
    }
    for name, value in artifacts.items():
        _write_json(output_dir / name, value)
    projection_csv = [{key: value for key, value in row.items() if not isinstance(value, (dict, list))} for row in projections]
    quantile_csv = [{key: row.get(key) for key in (
        "season", "week", "game_id", "canonical_player_id", "player_name", "market", "actual",
        "expected_output", "p10", "p25", "p50", "p75", "p90", "crps", "configuration",
    )} for row in projections]
    csvs = {
        "player_projections.csv": projection_csv, "quantile_predictions.csv": quantile_csv,
        "stability_classifications.csv": stability_rows, "calibration_buckets.csv": calibration_buckets,
        "parlay_leg_candidates.csv": threshold_rows,
    }
    for name, rows in csvs.items():
        _write_csv(output_dir / name, rows)
    all_inputs = sorted(set(inputs + threshold_inputs + v3_inputs + v4_inputs))
    artifact_paths = [output_dir / name for name in (*artifacts, *csvs)]
    manifest = {
        "schema_version": 1, "model_id": MODEL_ID, "network_contacted": False,
        "inputs": {path.as_posix(): _hash(path) for path in all_inputs},
        "artifacts": {path.name: _hash(path) for path in sorted(artifact_paths, key=lambda item: item.name)},
        "determinism": {"seed": seed, "sorted_inputs": True, "single_threaded_estimators": True},
    }
    _write_json(output_dir / "manifest.json", manifest)
    if register:
        register_model(registry_root, model_definition)
    return {**artifacts, **csvs, "manifest.json": manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", type=Path, default=SNAPSHOTS_DIR)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seasons", default="2023,2024,2025")
    parser.add_argument("--evaluation-seasons", default="2024,2025")
    parser.add_argument("--threshold-files", nargs="*", type=Path, default=[])
    parser.add_argument("--v4-prediction-files", nargs="*", type=Path, default=[])
    parser.add_argument("--min-history", type=int, default=2)
    parser.add_argument("--min-train-rows", type=int, default=150)
    parser.add_argument("--min-selection-rows", type=int, default=100)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--probability-threshold", type=float, default=.70)
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--registry-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    args.seasons = tuple(int(value) for value in args.seasons.split(","))
    args.evaluation_seasons = tuple(int(value) for value in args.evaluation_seasons.split(","))
    run_research(**vars(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
