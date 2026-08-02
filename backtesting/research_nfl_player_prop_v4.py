"""Train and evaluate the leakage-safe NFL Player Prop V4 research candidate.

V4 learns market-specific conditional means and residual variance from completed
historical games, selects distribution backends from walk-forward likelihood,
explains predictions, and emits research-only Kelly sizing.  It never promotes
itself or uses evaluation outcomes as training inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import numpy as np
from scipy import stats
from sklearn.base import clone
from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor, VotingRegressor
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import ElasticNet
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .analyze_nfl_player_prop_errors import _semantic_hash, _write_csv, _write_json, load_joined_analysis_rows
from .config import SNAPSHOTS_DIR
from .evaluate_nfl_player_props import probability_metrics
from .experiment_results import build_experiment_result, write_reliability_svg
from .game_matching import normalize_team
from .model_registry import DEFAULT_ROOT, git_commit, register_experiment, register_model
from .nfl_simulation import PLAYER_MARKETS
from .research_nfl_player_prop_models import enrich_context


MODEL_ID = "nfl_prop_v4_research_v1"
SCHEMA_VERSION = 1
DISTRIBUTIONS = ("normal", "student_t", "lognormal", "gamma", "poisson",
                 "negative_binomial", "zero_inflated_poisson",
                 "zero_inflated_negative_binomial")
FEATURES = (
    "history_games", "rolling_mean_3", "rolling_mean_5", "rolling_mean_all",
    "rolling_std_5", "rolling_std_all", "last_value", "recent_form_delta",
    "zero_rate", "usage_mean_3", "usage_mean_all", "team_market_mean_5",
    "opponent_allowed_mean_5", "team_plays_mean_5", "qb_strength_mean_5",
    "home", "season_week", "game_total", "team_spread", "implied_team_total",
)
RICH_FEATURE_COVERAGE = {
    "rolling_usage": "DERIVED_AVAILABLE",
    "snap_share": "NOT_AVAILABLE",
    "route_participation": "NOT_AVAILABLE",
    "target_share": "DERIVED_AVAILABLE",
    "opponent_defense": "DERIVED_AVAILABLE",
    "vegas_total_spread": "EVALUATION_ONLY_NO_2024_TRAINING_HISTORY",
    "implied_team_total": "EVALUATION_ONLY_NO_2024_TRAINING_HISTORY",
    "qb_strength": "DERIVED_TEAM_PASSING_HISTORY",
    "offensive_line": "NOT_AVAILABLE",
    "neutral_situation_pace": "NOT_AVAILABLE",
    "team_plays_pace_proxy": "DERIVED_AVAILABLE",
    "weather": "NOT_AVAILABLE",
    "opponent_wr1_defense": "NOT_AVAILABLE",
    "opponent_rb_receiving_defense": "NOT_AVAILABLE",
    "qb_pressure_rate": "NOT_AVAILABLE",
    "pass_rate_over_expectation": "NOT_AVAILABLE",
    "offensive_line_grades": "NOT_AVAILABLE",
    "defensive_line_grades": "NOT_AVAILABLE",
    "red_zone_usage": "NOT_AVAILABLE",
    "goal_line_carries": "NOT_AVAILABLE",
    "explosive_play_rate": "NOT_AVAILABLE",
}


def _number(row: dict[str, Any], *names: str) -> float | None:
    stats_row = row.get("stats") if isinstance(row.get("stats"), dict) else {}
    for name in names:
        value = row.get(name, stats_row.get(name))
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    return None


def _market_values(row: dict[str, Any]) -> dict[str, float]:
    category = str(row.get("category") or row.get("stat_category") or "").lower()
    values: dict[str, float] = {}
    if category == "passing" or _number(row, "passing_yards") is not None:
        aliases = {"passing_yards": ("passing_yards",),
                   "passing_tds": ("passing_tds", "passing_touchdowns")}
    elif category == "rushing" or _number(row, "rushing_yards") is not None:
        aliases = {"rushing_attempts": ("rushing_attempts", "attempts"),
                   "rushing_yards": ("rushing_yards",)}
    elif category == "receiving" or _number(row, "receiving_yards") is not None:
        aliases = {"receptions": ("receptions",), "receiving_yards": ("receiving_yards",)}
    else:
        aliases = {}
    for market, names in aliases.items():
        value = _number(row, *names)
        if value is not None:
            values[market] = value
    return values


def _usage_value(row: dict[str, Any], market: str) -> tuple[str, float] | None:
    if market.startswith("passing_"):
        value = _number(row, "passing_attempts", "attempts")
        return ("pass_attempts", value) if value is not None else None
    if market.startswith("rushing_"):
        value = _number(row, "rushing_attempts", "attempts")
        return ("rush_attempts", value) if value is not None else None
    value = _number(row, "targets")
    return ("targets", value) if value is not None else None


def _mean(values: Iterable[float]) -> float | None:
    present = list(values)
    return sum(present) / len(present) if present else None


def _std(values: list[float]) -> float | None:
    return float(np.std(values)) if len(values) >= 2 else None


def _rolling_features(values: list[float]) -> dict[str, float | None]:
    recent3, recent5 = values[-3:], values[-5:]
    all_mean = _mean(values)
    return {
        "history_games": float(len(values)),
        "rolling_mean_3": _mean(recent3), "rolling_mean_5": _mean(recent5),
        "rolling_mean_all": all_mean, "rolling_std_5": _std(recent5),
        "rolling_std_all": _std(values), "last_value": values[-1] if values else None,
        "recent_form_delta": None if not recent3 or all_mean is None else float(_mean(recent3) - all_mean),
        "zero_rate": sum(value == 0 for value in values) / len(values) if values else None,
    }


def _load_history(root: Path, seasons: tuple[int, ...]) -> tuple[list[dict[str, Any]], list[Path]]:
    aggregated: dict[tuple[int, int, str, str], dict[str, Any]] = {}
    inputs: list[Path] = []
    games: dict[str, dict[str, Any]] = {}
    for season in seasons:
        season_dir = root / "nfl" / str(season)
        for directory in sorted(season_dir.glob("week_*")):
            try:
                week = int(directory.name.split("_")[-1])
            except ValueError:
                continue
            games_path, stats_path = directory / "games.json", directory / "player_stats.json"
            if games_path.exists():
                inputs.append(games_path)
                for game in json.loads(games_path.read_text(encoding="utf-8")):
                    games[str(game.get("game_id"))] = game
            if not stats_path.exists():
                continue
            inputs.append(stats_path)
            for raw in json.loads(stats_path.read_text(encoding="utf-8")):
                if str(raw.get("record_role") or "").lower() != "completed_game_history":
                    continue
                game_id = str(raw.get("game_id") or "")
                player_id = str(raw.get("canonical_player_id") or raw.get("player_id") or "").strip()
                if not game_id or not player_id:
                    continue
                key = (season, week, game_id, player_id)
                item = aggregated.setdefault(key, {"season": season, "week": week, "game_id": game_id,
                    "player_id": player_id, "player_name": raw.get("player_name") or raw.get("player"),
                    "team": normalize_team(raw.get("team")), "values": {}, "usage": {}})
                item["values"].update(_market_values(raw))
                for market in _market_values(raw):
                    usage = _usage_value(raw, market)
                    if usage is not None:
                        item["usage"][market] = usage
    for item in aggregated.values():
        game = games.get(item["game_id"], {})
        home, away, team = normalize_team(game.get("home_team")), normalize_team(game.get("away_team")), item["team"]
        item["opponent"] = away if team == home else home if team == away else "UNKNOWN"
        item["home"] = 1.0 if team == home else 0.0 if team == away else None
        item["kickoff"] = str(game.get("kickoff_time") or game.get("commence_time") or "")
    rows = sorted(aggregated.values(), key=lambda row: (row["season"], row["week"], row["kickoff"], row["game_id"], row["player_id"]))
    return rows, sorted(set(inputs))


class FeatureState:
    def __init__(self) -> None:
        self.player: dict[tuple[str, str], list[float]] = defaultdict(list)
        self.usage: dict[tuple[str, str], list[float]] = defaultdict(list)
        self.team_market: dict[tuple[str, str], list[float]] = defaultdict(list)
        self.defense_allowed: dict[tuple[str, str], list[float]] = defaultdict(list)
        self.team_plays: dict[str, list[float]] = defaultdict(list)
        self.team_passing: dict[str, list[float]] = defaultdict(list)

    def features(self, *, player_id: str, team: str, opponent: str, market: str,
                 season: int, week: int, home: float | None,
                 game_total: float | None = None, team_spread: float | None = None,
                 implied_team_total: float | None = None) -> dict[str, float | None]:
        values = self.player[(player_id, market)]
        usage = self.usage[(player_id, market)]
        result = _rolling_features(values)
        result.update({
            "usage_mean_3": _mean(usage[-3:]), "usage_mean_all": _mean(usage),
            "team_market_mean_5": _mean(self.team_market[(team, market)][-5:]),
            "opponent_allowed_mean_5": _mean(self.defense_allowed[(opponent, market)][-5:]),
            "team_plays_mean_5": _mean(self.team_plays[team][-5:]),
            "qb_strength_mean_5": _mean(self.team_passing[team][-5:]),
            "home": home, "season_week": float((season - 2020) * 20 + week),
            "game_total": game_total, "team_spread": team_spread,
            "implied_team_total": implied_team_total,
        })
        return result

    def update_game(self, game_rows: list[dict[str, Any]]) -> None:
        team_totals: dict[tuple[str, str], float] = defaultdict(float)
        team_usage_totals: dict[tuple[str, str], float] = defaultdict(float)
        for row in game_rows:
            for market, value in row["values"].items():
                team_totals[(row["team"], market)] += value
            # One stat row can feed multiple markets (targets feed receptions
            # and receiving yards). Count each raw usage denominator once.
            raw_usage = {name: value for name, value in row["usage"].values()}
            for name, value in raw_usage.items():
                team_usage_totals[(row["team"], name)] += value
        for row in game_rows:
            for market, value in row["values"].items():
                self.player[(row["player_id"], market)].append(value)
                usage = row["usage"].get(market)
                if usage:
                    denominator = team_usage_totals[(row["team"], usage[0])]
                    if denominator > 0:
                        self.usage[(row["player_id"], market)].append(usage[1] / denominator)
        teams = sorted({row["team"] for row in game_rows})
        for team in teams:
            opponent = next((row["opponent"] for row in game_rows if row["team"] == team), "UNKNOWN")
            for market in PLAYER_MARKETS:
                total = team_totals.get((team, market))
                if total is not None:
                    self.team_market[(team, market)].append(total)
                    self.defense_allowed[(opponent, market)].append(total)
            pass_attempts = team_usage_totals.get((team, "pass_attempts"), 0.0)
            rush_attempts = team_usage_totals.get((team, "rush_attempts"), 0.0)
            if pass_attempts or rush_attempts:
                self.team_plays[team].append(pass_attempts + rush_attempts)
            passing_yards = team_totals.get((team, "passing_yards"))
            if passing_yards is not None:
                self.team_passing[team].append(passing_yards)


def build_training_samples(root: Path, seasons: tuple[int, ...], min_history: int = 2) -> tuple[list[dict[str, Any]], FeatureState, list[Path]]:
    history, inputs = _load_history(root, seasons)
    state = FeatureState(); samples: list[dict[str, Any]] = []
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        grouped[(row["season"], row["week"])].append(row)
    for week_key in sorted(grouped):
        week_rows = grouped[week_key]
        # Every sample in a week is built before any result from that week is
        # added, preventing early-game outcomes from leaking into later games.
        for row in week_rows:
            for market, target in sorted(row["values"].items()):
                features = state.features(player_id=row["player_id"], team=row["team"], opponent=row["opponent"],
                    market=market, season=row["season"], week=row["week"], home=row["home"])
                if int(features["history_games"] or 0) >= min_history:
                    samples.append({"season": row["season"], "week": row["week"], "game_id": row["game_id"],
                        "player_id": row["player_id"], "player_name": row["player_name"], "team": row["team"],
                        "opponent": row["opponent"], "market": market, "target": target, "features": features})
        by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in week_rows: by_game[row["game_id"]].append(row)
        for game_id in sorted(by_game): state.update_game(by_game[game_id])
    return samples, state, inputs


def _matrix(rows: list[dict[str, Any]]) -> np.ndarray:
    return np.array([[np.nan if row["features"].get(name) is None else float(row["features"][name]) for name in FEATURES]
                     for row in rows], dtype=float)


def _ensemble(seed: int) -> VotingRegressor:
    elastic = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(),
                            ElasticNet(alpha=.05, l1_ratio=.2, max_iter=5000, random_state=seed))
    forest = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), RandomForestRegressor(
        n_estimators=80, min_samples_leaf=6, max_features=.75, random_state=seed, n_jobs=1))
    boost = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), HistGradientBoostingRegressor(
        max_iter=100, max_leaf_nodes=20, min_samples_leaf=15, l2_regularization=1.0, random_state=seed))
    return VotingRegressor([("elastic", elastic), ("random_forest", forest),
                            ("hist_gradient_boosting", boost)], n_jobs=1)


def _variance_model(seed: int) -> TransformedTargetRegressor:
    regressor = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), RandomForestRegressor(
        n_estimators=100, min_samples_leaf=8, max_features=.8, random_state=seed, n_jobs=1))
    return TransformedTargetRegressor(regressor=regressor, func=np.log1p, inverse_func=np.expm1)


def _walk_forward(samples: list[dict[str, Any]], market: str, seed: int,
                  min_train_rows: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    values = [row for row in samples if row["market"] == market]
    weeks = sorted({int(row["week"]) for row in values})
    candidate_weeks = weeks[6::2]
    if weeks and weeks[-1] not in candidate_weeks:
        candidate_weeks.append(weeks[-1])
    predictions: list[dict[str, Any]] = []
    importances: list[dict[str, Any]] = []
    for test_week in candidate_weeks:
        train = [row for row in values if row["week"] < test_week]
        test = [row for row in values if row["week"] == test_week]
        if len(train) < min_train_rows or len(test) < 10:
            continue
        model = _ensemble(seed + test_week); xtrain, xtest = _matrix(train), _matrix(test)
        ytrain = np.array([row["target"] for row in train]); ytest = np.array([row["target"] for row in test])
        model.fit(xtrain, ytrain); predicted = np.maximum(0.0, model.predict(xtest))
        for row, estimate, actual in zip(test, predicted, ytest):
            predictions.append({**row, "predicted_mean": float(estimate), "residual": float(actual - estimate),
                                "train_weeks": sorted({int(item["week"]) for item in train}), "test_week": test_week})
        result = permutation_importance(model, xtest, ytest, scoring="neg_mean_absolute_error",
                                        n_repeats=5, random_state=seed + test_week, n_jobs=1)
        for name, value, deviation in zip(FEATURES, result.importances_mean, result.importances_std):
            importances.append({"market": market, "test_week": test_week, "feature": name,
                                "importance": float(value), "importance_stddev": float(deviation),
                                "train_rows": len(train), "test_rows": len(test)})
    return predictions, importances


def _nb_params(mean: float, variance: float) -> tuple[float, float]:
    variance = max(variance, mean + 1e-6)
    n = max(1e-6, mean * mean / (variance - mean))
    return n, n / (n + mean)


def _distribution(family: str, mean: float, variance: float, zero_rate: float) -> tuple[Any, Any, Any, bool]:
    mean, variance = max(0.0, mean), max(1e-6, variance)
    if family == "normal":
        d = stats.norm(mean, math.sqrt(variance)); return d.cdf, d.pdf, d.ppf, False
    if family == "student_t":
        degrees = 5; scale = math.sqrt(variance * (degrees - 2) / degrees)
        d = stats.t(df=degrees, loc=mean, scale=scale); return d.cdf, d.pdf, d.ppf, False
    if family == "lognormal":
        adjusted = max(mean, 1e-6); sigma2 = math.log1p(variance / adjusted ** 2)
        d = stats.lognorm(s=math.sqrt(max(sigma2, 1e-9)), scale=math.exp(math.log(adjusted) - sigma2 / 2))
        return d.cdf, d.pdf, d.ppf, False
    if family == "gamma":
        adjusted = max(mean, 1e-6); d = stats.gamma(a=max(1e-6, adjusted ** 2 / variance), scale=variance / adjusted)
        return d.cdf, d.pdf, d.ppf, False
    if family in {"poisson", "zero_inflated_poisson"}:
        pi = min(.8, max(0.0, zero_rate)) if family.startswith("zero_") else 0.0
        conditional = mean / max(1e-9, 1 - pi); d = stats.poisson(conditional)
    else:
        pi = min(.8, max(0.0, zero_rate)) if family.startswith("zero_") else 0.0
        conditional = mean / max(1e-9, 1 - pi)
        n, p = _nb_params(conditional, max(conditional + 1e-6, variance / max(1e-9, 1 - pi)))
        d = stats.nbinom(n, p)
    def cdf(x: float) -> float: return pi + (1 - pi) * float(d.cdf(x)) if x >= 0 else 0.0
    def pmf(x: float) -> float: return (pi if x == 0 else 0.0) + (1 - pi) * float(d.pmf(x))
    def ppf(q: float) -> float:
        if q <= pi: return 0.0
        return float(d.ppf((q - pi) / max(1e-9, 1 - pi)))
    return cdf, pmf, ppf, True


def _likelihood(family: str, actual: float, mean: float, variance: float, zero_rate: float) -> float:
    cdf, density, _ppf, discrete = _distribution(family, mean, variance, zero_rate)
    if discrete:
        likelihood = float(density(round(actual))) if abs(actual - round(actual)) < 1e-9 else 1e-15
        return max(1e-15, likelihood) if math.isfinite(likelihood) else 1e-15
    return max(1e-15, float(cdf(actual + .5)) - float(cdf(actual - .5)))


def _probabilities(family: str, line: float, mean: float, variance: float,
                   zero_rate: float) -> dict[str, float]:
    cdf, density, _ppf, discrete = _distribution(family, mean, variance, zero_rate)
    if discrete:
        over = 1 - float(cdf(math.floor(line))); under = float(cdf(math.ceil(line) - 1))
        push = float(density(round(line))) if abs(line - round(line)) < 1e-9 else 0.0
    else:
        over, under, push = 1 - float(cdf(line)), float(cdf(line)), 0.0
    total = max(1e-12, over + under + push)
    return {"OVER": max(0.0, over / total), "UNDER": max(0.0, under / total), "PUSH": max(0.0, push / total)}


def _select_distributions(oof: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for market in PLAYER_MARKETS:
        rows = [row for row in oof if row["market"] == market and row.get("predicted_variance") is not None]
        weeks = sorted({row["test_week"] for row in rows})
        scores = []
        for family in DISTRIBUTIONS:
            losses = [-math.log(_likelihood(family, float(row["target"]), float(row["predicted_mean"]),
                                                   float(row["predicted_variance"]), float(row["features"].get("zero_rate") or 0)))
                      for row in rows]
            scores.append({"family": family, "average_nll": _mean(losses), "count": len(losses)})
        eligible = len(weeks) >= 4 and len(rows) >= 100
        ranked = sorted(scores, key=lambda row: (float("inf") if row["average_nll"] is None else row["average_nll"], row["family"]))
        for rank, item in enumerate(ranked, 1): item["rank"] = rank
        output.append({"market": market, "status": "SELECTED" if eligible else "INSUFFICIENT_HISTORY",
                       "selected_family": ranked[0]["family"] if eligible else "normal",
                       "selection_policy": "lowest walk-forward interval/discrete negative log likelihood",
                       "walk_forward_weeks": weeks, "rows": len(rows), "candidates": ranked})
    return output


def _cross_fit_variance(oof: list[dict[str, Any]], seed: int,
                        min_train_rows: int = 30) -> None:
    """Attach variance estimates trained only on earlier residual folds."""
    for market in PLAYER_MARKETS:
        rows = [row for row in oof if row["market"] == market]
        weeks = sorted({int(row["test_week"]) for row in rows})
        for test_week in weeks:
            prior_weeks = [week for week in weeks if week < test_week]
            train = [row for row in rows if int(row["test_week"]) < test_week]
            test = [row for row in rows if int(row["test_week"]) == test_week]
            if len(prior_weeks) < 2 or len(train) < min_train_rows or not test:
                continue
            model = _variance_model(seed + test_week)
            model.fit(_matrix(train), np.array([row["residual"] ** 2 + .25 for row in train]))
            predicted = np.maximum(.25, model.predict(_matrix(test)))
            for row, value in zip(test, predicted):
                row["predicted_variance"] = float(value)


def _fit_market(samples: list[dict[str, Any]], oof: list[dict[str, Any]], market: str,
                seed: int) -> tuple[VotingRegressor, TransformedTargetRegressor, np.ndarray]:
    rows = [row for row in samples if row["market"] == market]
    model = _ensemble(seed); matrix = _matrix(rows); target = np.array([row["target"] for row in rows])
    model.fit(matrix, target)
    residual_rows = [row for row in oof if row["market"] == market]
    variance = _variance_model(seed)
    if len(residual_rows) >= 30:
        variance.fit(_matrix(residual_rows), np.array([row["residual"] ** 2 + .25 for row in residual_rows]))
    else:
        fitted = np.maximum(.25, np.full(len(rows), np.var(target)))
        variance.fit(matrix, fitted)
    medians = np.array([float(np.nanmedian(matrix[:, index])) if np.any(~np.isnan(matrix[:, index])) else 0.0
                        for index in range(matrix.shape[1])])
    return model, variance, medians


def _local_explanation(model: VotingRegressor, vector: np.ndarray, medians: np.ndarray) -> list[dict[str, float | str]]:
    base = float(model.predict(vector.reshape(1, -1))[0]); explanations = []
    for index, name in enumerate(FEATURES):
        if np.isnan(vector[index]): continue
        ablated = vector.copy(); ablated[index] = medians[index]
        explanations.append({"feature": name, "value": float(vector[index]),
                             "contribution": base - float(model.predict(ablated.reshape(1, -1))[0])})
    return sorted(explanations, key=lambda row: (-abs(float(row["contribution"])), str(row["feature"])))[:8]


def _kelly(probability: float, push: float, decimal_odds: float | None,
           fraction: float, cap: float) -> dict[str, Any]:
    if decimal_odds is None or decimal_odds <= 1:
        return {"expected_value": None, "full_kelly": 0.0, "fractional_kelly": 0.0, "status": "MISSING_PRICE"}
    b = decimal_odds - 1; loss = max(0.0, 1 - probability - push)
    expected = probability * b - loss
    full = max(0.0, expected / b)
    return {"expected_value": expected, "full_kelly": full,
            "fractional_kelly": min(cap, fraction * full), "status": "RESEARCH_ONLY_UNCALIBRATED"}


def _probability_bucket(probability: float) -> str:
    lower = min(9, int(max(0.0, min(.999999, probability)) * 10)) * 10
    return f"{lower}-{lower + 10}%"


def _metric_arrays(rows: list[dict[str, Any]], field: str, edge_field: str) -> dict[str, float | None]:
    scored = [{**row, "model_probability": row[field]} for row in rows]
    metric = probability_metrics(scored, "model_probability")
    bets = [row for row in scored if float(row.get(edge_field) or -999) >= .05 and row.get("grade") in {"WIN", "LOSS"}]
    return {"brier_score": metric["brier_score"], "log_loss": metric["log_loss"], "ece": metric["ece"],
            "roi": sum(float(row.get("profit_units") or 0) for row in bets) / len(bets) if bets else None}


def _paired_comparison(rows: list[dict[str, Any]], seed: int, draws: int = 1000) -> dict[str, Any]:
    candidate = _metric_arrays(rows, "model_probability", "candidate_edge")
    baseline = _metric_arrays(rows, "baseline_probability", "baseline_edge")
    estimates = {name: candidate[name] - baseline[name] if candidate[name] is not None and baseline[name] is not None else None
                 for name in candidate}
    games: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows: games[str(row["game_id"])].append(row)
    keys = sorted(games); rng = random.Random(seed); boot = defaultdict(list)
    if len(keys) >= 2:
        for _ in range(draws):
            sample = [row for _key in keys for row in games[rng.choice(keys)]]
            cm, bm = _metric_arrays(sample, "model_probability", "candidate_edge"), _metric_arrays(sample, "baseline_probability", "baseline_edge")
            for name in estimates:
                if cm[name] is not None and bm[name] is not None: boot[name].append(cm[name] - bm[name])
    deltas = {}
    for name, estimate in estimates.items():
        ordered = sorted(boot[name])
        interval = [ordered[int(.025 * len(ordered))], ordered[max(0, int(.975 * len(ordered)) - 1)]] if ordered else None
        deltas[name] = {"estimate": estimate, "ci_95": interval}
    return {"baseline_model_id": "nfl_game_baseline_v3", "paired_opportunities": True,
            "bootstrap_method": "deterministic_game_cluster_bootstrap", "bootstrap_draws": draws,
            "candidate_metrics": candidate, "baseline_metrics": baseline, "metric_deltas": deltas}


def _calibration_metric(probabilities: np.ndarray, outcomes: np.ndarray) -> dict[str, float]:
    rows = [{"grade": "WIN" if outcome else "LOSS", "probability": float(probability)}
            for probability, outcome in zip(probabilities, outcomes)]
    metric = probability_metrics(rows, "probability")
    return {"brier_score": metric["brier_score"], "log_loss": metric["log_loss"], "ece": metric["ece"]}


def _walk_forward_calibration_v4(rows: list[dict[str, Any]], seed: int,
                                 min_train_rows: int, min_test_rows: int = 20) -> dict[str, Any]:
    folds = []
    for market in PLAYER_MARKETS:
        values = [row for row in rows if row.get("market") == market and row.get("grade") in {"WIN", "LOSS"}]
        weeks = sorted({int(row["week"]) for row in values})
        for test_week in weeks:
            prior = [week for week in weeks if week < test_week]
            train = [row for row in values if int(row["week"]) < test_week]
            test = [row for row in values if int(row["week"]) == test_week]
            if len(prior) < 2 or len(train) < min_train_rows or len(test) < min_test_rows: continue
            ptrain = np.clip(np.array([row["model_probability"] for row in train]), 1e-6, 1 - 1e-6)
            ytrain = np.array([row["grade"] == "WIN" for row in train], dtype=int)
            ptest = np.clip(np.array([row["model_probability"] for row in test]), 1e-6, 1 - 1e-6)
            ytest = np.array([row["grade"] == "WIN" for row in test], dtype=int)
            if len(set(ytrain)) < 2: continue
            methods = {
                "isotonic": np.clip(IsotonicRegression(out_of_bounds="clip").fit(ptrain, ytrain).predict(ptest), 1e-6, 1 - 1e-6),
            }
            beta_x = np.column_stack((np.log(ptrain), np.log1p(-ptrain)))
            beta_test = np.column_stack((np.log(ptest), np.log1p(-ptest)))
            methods["beta"] = LogisticRegression(C=1e6, max_iter=2000, random_state=seed).fit(beta_x, ytrain).predict_proba(beta_test)[:, 1]
            logit_train = np.log(ptrain / (1 - ptrain)).reshape(-1, 1)
            logit_test = np.log(ptest / (1 - ptest)).reshape(-1, 1)
            methods["platt"] = LogisticRegression(C=1e6, max_iter=2000, random_state=seed).fit(logit_train, ytrain).predict_proba(logit_test)[:, 1]
            raw = _calibration_metric(ptest, ytest)
            for method, calibrated in methods.items():
                metric = _calibration_metric(np.clip(calibrated, 1e-6, 1 - 1e-6), ytest)
                folds.append({"market": market, "method": method, "test_week": test_week,
                              "train_weeks": prior, "train_rows": len(train), "test_rows": len(test),
                              "raw": raw, "calibrated": metric,
                              "brier_improvement": raw["brier_score"] - metric["brier_score"],
                              "ece_improvement": raw["ece"] - metric["ece"]})
    return {"status": "COMPLETE" if folds else "INSUFFICIENT_HISTORY",
            "reason": None if folds else "requires two prior evaluated weeks and historical prop lines",
            "supported_methods": ["isotonic", "beta", "platt"], "folds": folds}


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_model_definition(path: Path, distribution_selection: list[dict[str, Any]]) -> dict[str, Any]:
    record = {"schema_version": 1, "model_id": MODEL_ID, "sport": "nfl", "target": "player_props",
              "state": "experimental", "git_commit": git_commit(),
              "description": "Leakage-safe per-market ensemble mean and learned residual-variance research candidate.",
              "feature_set": {"version": "nfl-prop-v4-features-1", "features": list(FEATURES)},
              "distribution": {"family": "market_specific_walk_forward_selection",
                               "backends": {row["market"]: row["selected_family"] for row in distribution_selection}},
              "variance": {"method": "random_forest_oof_squared_residual"},
              "calibration": {"method": "per_market_isotonic_beta_or_platt", "status": "evidence_gated"},
              "ensemble": {"members": ["elastic_net", "random_forest", "hist_gradient_boosting"], "combiner": "equal_weight_mean"}}
    _write_json(path, record); return record


def run_v4(*, snapshot_root: Path, season_results_dir: Path, output_dir: Path,
           evaluation_season: int = 2025, start_week: int = 1, end_week: int = 18,
           training_seasons: tuple[int, ...] = (2024,), seed: int = 1729,
           simulations: int = 10000, min_train_rows: int = 100,
           kelly_fraction: float = .25, kelly_cap: float = .05,
           registry_root: Path | None = None, register: bool = False) -> dict[str, Any]:
    if not 0 <= kelly_fraction <= 1 or not 0 <= kelly_cap <= 1:
        raise ValueError("Kelly fraction and cap must be between zero and one")
    samples, state, history_inputs = build_training_samples(snapshot_root, training_seasons)
    joined, exclusions, input_paths = load_joined_analysis_rows(season=evaluation_season, start_week=start_week,
        end_week=end_week, snapshot_root=snapshot_root, season_results_dir=season_results_dir)
    joined, context_inputs = enrich_context(joined, snapshot_root, evaluation_season, start_week, end_week)
    input_paths += history_inputs + context_inputs
    oof: list[dict[str, Any]] = []; importance: list[dict[str, Any]] = []
    for market in PLAYER_MARKETS:
        market_oof, market_importance = _walk_forward(samples, market, seed, min_train_rows)
        oof += market_oof; importance += market_importance
    _cross_fit_variance(oof, seed)
    selection = _select_distributions(oof)
    selected = {row["market"]: row["selected_family"] for row in selection}
    fitted = {}; training_report = []
    for market in PLAYER_MARKETS:
        market_samples = [row for row in samples if row["market"] == market]
        market_oof = [row for row in oof if row["market"] == market]
        if len(market_samples) < min_train_rows: continue
        model, variance, medians = _fit_market(samples, oof, market, seed)
        fitted[market] = (model, variance, medians)
        if market_oof:
            actual = np.array([row["target"] for row in market_oof]); predicted = np.array([row["predicted_mean"] for row in market_oof])
            training_report.append({"market": market, "training_rows": len(market_samples), "walk_forward_rows": len(market_oof),
                "walk_forward_weeks": sorted({row["test_week"] for row in market_oof}),
                "mae": float(mean_absolute_error(actual, predicted)), "rmse": float(mean_squared_error(actual, predicted) ** .5),
                "r2": float(r2_score(actual, predicted))})
    base_cache = {}; predictions = []
    for row in joined:
        market = str(row["market"]); key = (str(row["game_id"]), str(row["canonical_player_id"]), market)
        if market not in fitted: continue
        if key not in base_cache:
            features = state.features(player_id=key[1], team=normalize_team(row.get("team")), opponent=normalize_team(row.get("opponent")),
                market=market, season=evaluation_season, week=int(row["week"]), home=1.0 if row.get("home_away") == "HOME" else 0.0,
                game_total=row.get("game_total"), team_spread=row.get("team_spread"), implied_team_total=row.get("implied_team_total"))
            holder = {"features": features}; vector = _matrix([holder])[0]; model, variance_model, medians = fitted[market]
            mean_prediction = max(0.0, float(model.predict(vector.reshape(1, -1))[0]))
            variance_prediction = max(.25, float(variance_model.predict(vector.reshape(1, -1))[0]))
            base_cache[key] = {"features": features, "predicted_mean": mean_prediction,
                "predicted_variance": variance_prediction, "distribution": selected[market],
                "explanation": _local_explanation(model, vector, medians)}
        base = base_cache[key]; probs = _probabilities(base["distribution"], float(row["line"]),
            base["predicted_mean"], base["predicted_variance"], float(base["features"].get("zero_rate") or 0))
        side = str(row["side"]).upper(); probability = probs[side]
        market_probability = float(row.get("no_vig_market_probability") or .5); edge = probability - market_probability
        sizing = _kelly(probability, probs["PUSH"], float(row["decimal_odds"]) if row.get("decimal_odds") is not None else None,
                        kelly_fraction, kelly_cap)
        explanation_key = "|".join(key)
        predictions.append({"season": int(row["season"]), "week": int(row["week"]),
            "game_id": row["game_id"], "canonical_player_id": row["canonical_player_id"],
            "player_name": row.get("player_name"), "team": row.get("team"), "opponent": row.get("opponent"),
            "market": market, "line": float(row["line"]), "side": side,
            "bookmaker": row.get("bookmaker"), "american_odds": row.get("american_odds"),
            "decimal_odds": row.get("decimal_odds"), "no_vig_market_probability": market_probability,
            "grade": row.get("grade"), "profit_units": row.get("profit_units"),
            "actual_stat": row.get("actual_stat"), "baseline_probability": float(row["model_probability"]),
            "model_probability": probability, "probability_bucket": _probability_bucket(probability),
            "push_probability": probs["PUSH"], "candidate_edge": edge,
            "baseline_edge": float(row.get("edge") or 0), "decision": "BET" if edge >= .05 else "PASS",
            "kelly": sizing, "predicted_mean": base["predicted_mean"],
            "predicted_variance": base["predicted_variance"], "distribution": base["distribution"],
            "explanation_key": explanation_key, "promotion_eligible": False, "research_only": True})
    calibration = _walk_forward_calibration_v4(predictions, seed, min_train_rows, 20)
    comparison = _paired_comparison(predictions, seed)
    output_dir.mkdir(parents=True, exist_ok=True)
    projections = [{"explanation_key": "|".join(key), "game_id": key[0], "canonical_player_id": key[1],
                    "market": key[2], **value} for key, value in sorted(base_cache.items())]
    artifacts = {
        "v4_summary.json": {"schema_version": 1, "model_id": MODEL_ID, "network_contacted": False,
            "training_seasons": list(training_seasons), "evaluation_season": evaluation_season,
            "evaluated_weeks": sorted({int(row["week"]) for row in predictions}),
            "training_samples": len(samples), "evaluation_opportunities": len(predictions),
            "exclusions": len(exclusions), "calibration_status": calibration["status"],
            "promotion_status": "RESEARCH_ONLY_INSUFFICIENT_EVALUATION_HISTORY",
            "baseline_comparison": comparison},
        "training_metrics.json": training_report, "distribution_selection.json": selection,
        "calibration_report.json": calibration, "permutation_importance.json": importance,
        "feature_coverage.json": {"requested_features": RICH_FEATURE_COVERAGE,
            "model_features": [{"feature": name, "training_coverage": sum(not np.isnan(row) for row in _matrix(samples)[:, index]) / len(samples) if samples else 0}
                               for index, name in enumerate(FEATURES)]},
        "v4_predictions.json": predictions, "v4_player_market_projections.json": projections,
    }
    for name, value in artifacts.items(): _write_json(output_dir / name, value)
    _write_csv(output_dir / "training_metrics.csv", training_report)
    _write_csv(output_dir / "permutation_importance.csv", importance)
    compact = [{"season": row["season"], "week": row["week"], "game_id": row["game_id"],
                "canonical_player_id": row["canonical_player_id"], "market": row["market"], "line": row["line"],
                "side": row["side"], "model_probability": row["model_probability"], "grade": row["grade"],
                "profit_units": row["profit_units"], "decision": row["decision"]} for row in predictions]
    _write_csv(output_dir / "v4_predictions.csv", compact)
    write_reliability_svg(output_dir / "reliability_plot.svg", probability_metrics(predictions, "model_probability")["bins"])
    artifact_paths = [output_dir / name for name in (*artifacts.keys(), "training_metrics.csv", "permutation_importance.csv", "v4_predictions.csv", "reliability_plot.svg")]
    input_hashes = {path.as_posix(): _semantic_hash(path) for path in sorted(set(input_paths))}
    experiment = build_experiment_result(model_id=MODEL_ID, season=evaluation_season,
        requested_weeks=(start_week, end_week), rows=predictions, seed=seed, simulations=simulations,
        configuration={"experiment_contract_version": "nfl-player-prop-v4-1", "training_seasons": list(training_seasons),
            "evaluation_season": evaluation_season, "start_week": start_week, "end_week": end_week,
            "seed": seed, "simulations": simulations, "min_train_rows": min_train_rows,
            "kelly_fraction": kelly_fraction, "kelly_cap": kelly_cap},
        input_hashes=input_hashes, artifact_paths=artifact_paths,
        training_strategy="expanding_2024_week_walk_forward_then_fit_all_prior_season",
        training_weeks=sorted({int(row["week"]) for row in samples}), leakage_safe=True, out_of_sample=True)
    experiment["baseline_comparison"] = comparison
    _write_json(output_dir / "experiment_result.json", experiment)
    model_definition = _write_model_definition(output_dir / "model_definition.json", selection)
    manifest_artifacts = [*artifact_paths, output_dir / "experiment_result.json", output_dir / "model_definition.json"]
    manifest = {"schema_version": 1, "network_contacted": False, "inputs": input_hashes,
                "artifacts": {path.name: _hash(path) for path in sorted(manifest_artifacts, key=lambda value: value.name)}}
    _write_json(output_dir / "v4_manifest.json", manifest)
    if register:
        root = registry_root or DEFAULT_ROOT
        register_model(root, model_definition); register_experiment(root, experiment)
    return {**artifacts, "experiment_result.json": experiment, "model_definition.json": model_definition,
            "v4_manifest.json": manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", type=Path, default=SNAPSHOTS_DIR)
    parser.add_argument("--season-results-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--evaluation-season", type=int, default=2025)
    parser.add_argument("--start-week", type=int, default=1); parser.add_argument("--end-week", type=int, default=18)
    parser.add_argument("--training-seasons", default="2024")
    parser.add_argument("--seed", type=int, default=1729); parser.add_argument("--simulations", type=int, default=10000)
    parser.add_argument("--min-train-rows", type=int, default=100)
    parser.add_argument("--kelly-fraction", type=float, default=.25); parser.add_argument("--kelly-cap", type=float, default=.05)
    parser.add_argument("--registry-root", type=Path, default=DEFAULT_ROOT); parser.add_argument("--register", action="store_true")
    args = parser.parse_args(argv); args.training_seasons = tuple(int(value) for value in args.training_seasons.split(","))
    run_v4(**vars(args)); return 0


if __name__ == "__main__": raise SystemExit(main())
