"""Generate outcome-free System A candidates for the frozen shadow ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression

from .calibrate_nfl_distribution_probabilities import _fit_predict, _select_method
from .forward_shadow_nfl_player_props import _digest, _file_hash, _write_json
from .game_matching import normalize_team, parse_dt
from .research_nfl_player_stat_distributions import (
    FEATURE_SETS, QUANTILES, RICH_FEATURES, DistributionFeatureState,
    _distribution_quantiles, _family_probability, _fit_fold_models,
    _load_system_a_history, _matrix, _non_crossing, _piecewise_cdf,
    build_distribution_samples,
)


CONFIG_ID = "nfl_system_a_shadow_prediction_config_v1"
LAUNCH_MARKETS = ("receptions", "receiving_yards", "rushing_yards")


def freeze_prediction_config(*, experiment_path: Path, calibration_rows_path: Path,
                             price_history_path: Path, shadow_policy_path: Path,
                             output_path: Path) -> dict[str, Any]:
    experiment = json.loads(experiment_path.read_text(encoding="utf-8"))
    calibration = json.loads(calibration_rows_path.read_text(encoding="utf-8"))
    policy = json.loads(shadow_policy_path.read_text(encoding="utf-8"))
    configurations = {}
    source_folds = {}
    for market in LAUNCH_MARKETS:
        folds = [row for row in experiment["folds"] if row.get("market") == market and
                 row.get("status") == "COMPLETE"]
        if not folds:
            raise ValueError(f"no complete frozen distribution fold for {market}")
        fold = max(folds, key=lambda row: (int(row["test_season"]), int(row["test_week"])))
        configurations[market] = fold["selected_configuration"]
        source_folds[market] = {"test_season": fold["test_season"], "test_week": fold["test_week"],
                                "selection": fold["selection"]}
    calibration_methods = {}
    for market in LAUNCH_MARKETS:
        for side in ("OVER", "UNDER"):
            rows = [row for row in calibration if row["market"] == market and row["side"] == side
                    and row["result"] in {"WIN", "LOSS"}]
            method, evidence = _select_method(rows, min_fit_rows=250, min_validation_rows=100,
                                              validation_periods=4)
            calibration_methods[f"{market}:{side}"] = {"method": method, "evidence": evidence,
                                                        "fit_rows": len(rows)}
    configuration = {
        "schema_version": 1, "config_id": CONFIG_ID, "state": "FROZEN_SHADOW_ONLY",
        "seed": int(experiment["configuration"]["seed"]),
        "training_seasons": list(experiment["configuration"]["seasons"]),
        "min_history": int(experiment["configuration"]["min_history"]),
        "distribution_configuration_by_market": configurations,
        "distribution_source_folds": source_folds,
        "calibration_by_market_side": calibration_methods,
        "residual_configuration": policy["configuration"],
        "minimum_expected_value": policy["minimum_expected_value"],
        "policy_fingerprint": policy["policy_fingerprint"],
        "inputs": {path.as_posix(): _file_hash(path) for path in (
            experiment_path, calibration_rows_path, price_history_path, shadow_policy_path)},
        "temporal_contract": "FULL_HISTORY_FIT_ENDS_BEFORE_EACH_FUTURE_PREDICTION_CUTOFF",
        "production_wagering_authorized": False,
    }
    configuration["configuration_fingerprint"] = _digest(configuration)
    if output_path.exists() and json.loads(output_path.read_text(encoding="utf-8")) != configuration:
        raise ValueError("shadow prediction configuration already exists with different content")
    if not output_path.exists():
        _write_json(output_path, configuration)
    return configuration


def _quote_timestamp(row: dict[str, Any]):
    return parse_dt(row.get("quote_timestamp") or row.get("provider_snapshot_timestamp") or
                    row.get("snapshot_timestamp") or row.get("captured_at"))


def prepare_quote_pairs(quotes: Sequence[dict[str, Any]], games: Sequence[dict[str, Any]], *,
                        generated_at: str) -> list[dict[str, Any]]:
    """Return complete exact-line pairs with consensus no-vig and best side prices."""
    generated = parse_dt(generated_at)
    if generated is None:
        raise ValueError("generated_at must be valid")
    game_index = {str(row["game_id"]): row for row in games}
    by_book: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = defaultdict(dict)
    for raw in quotes:
        if any(field in raw for field in ("actual", "outcome", "grade", "result", "profit_units")):
            raise ValueError("pregame quote contains outcome-bearing fields")
        market, side = str(raw.get("market")), str(raw.get("side") or raw.get("selection")).upper()
        if market not in LAUNCH_MARKETS or side not in {"OVER", "UNDER"}:
            continue
        game = game_index.get(str(raw.get("game_id")))
        if game is None:
            raise ValueError(f"quote game is missing: {raw.get('game_id')}")
        if any(game.get(field) is not None for field in ("final_home_score", "final_away_score", "outcome")) or \
                str(game.get("status") or "").casefold() in {"final", "completed", "post"}:
            raise ValueError("future shadow game contains completed outcome state")
        cutoff = parse_dt(game.get("prediction_cutoff") or game.get("prediction_timestamp") or game.get("kickoff_time"))
        stamp = _quote_timestamp(raw)
        if cutoff is None or stamp is None or stamp > cutoff or generated > cutoff or stamp > generated:
            raise ValueError("quote or generation timestamp violates prediction cutoff")
        player = str(raw.get("canonical_player_id") or raw.get("player_id") or "")
        if not player:
            raise ValueError("quote has no canonical player id")
        base = (int(raw["season"]), int(raw["week"]), str(raw["game_id"]), player,
                market, float(raw["line"]))
        book = str(raw.get("bookmaker") or raw.get("sportsbook") or "")
        if not book:
            raise ValueError("quote has no bookmaker")
        decimal = float(raw.get("decimal_odds") or 0)
        if decimal <= 1:
            raise ValueError("quote decimal odds must exceed one")
        key = (*base, book)
        if side in by_book[key]:
            raise ValueError(f"duplicate quote side for {key + (side,)}")
        by_book[key][side] = {**raw, "side": side, "decimal_odds": decimal,
                              "quote_timestamp": stamp.isoformat().replace("+00:00", "Z"),
                              "prediction_cutoff": cutoff.isoformat().replace("+00:00", "Z")}
    grouped: dict[tuple[Any, ...], list[tuple[str, dict[str, dict[str, Any]], float, float]]] = defaultdict(list)
    for key, sides in by_book.items():
        if set(sides) != {"OVER", "UNDER"}:
            continue
        over_i, under_i = 1 / sides["OVER"]["decimal_odds"], 1 / sides["UNDER"]["decimal_odds"]
        grouped[key[:-1]].append((key[-1], sides, over_i / (over_i + under_i), under_i / (over_i + under_i)))
    output = []
    for base in sorted(grouped):
        books = grouped[base]
        teams = {normalize_team(sides[side].get("team")) for _book, sides, _op, _up in books
                 for side in ("OVER", "UNDER")}
        if len(teams) != 1 or not next(iter(teams), ""):
            raise ValueError(f"quote pair has missing or conflicting teams for {base}")
        best = {side: max((sides[side] for _book, sides, _op, _up in books),
                          key=lambda row: (float(row["decimal_odds"]), str(row.get("bookmaker") or row.get("sportsbook"))))
                for side in ("OVER", "UNDER")}
        output.append({
            "base_key": list(base), "season": base[0], "week": base[1], "game_id": base[2],
            "canonical_player_id": base[3], "market": base[4], "line": base[5],
            "team": next(iter(teams)),
            "player_name": best["OVER"].get("canonical_player_name") or best["OVER"].get("player_name"),
            "consensus_no_vig_over": float(np.mean([item[2] for item in books])),
            "consensus_no_vig_under": float(np.mean([item[3] for item in books])),
            "best_over": best["OVER"], "best_under": best["UNDER"], "complete_books": len(books),
        })
    return output


def _history_state(snapshot_root: Path, system_a_dir: Path, seasons: Sequence[int]) -> tuple[DistributionFeatureState, list[Path]]:
    history, inputs, _audit = _load_system_a_history(snapshot_root, system_a_dir, seasons)
    state = DistributionFeatureState()
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        grouped[(int(row["season"]), int(row["week"]))].append(row)
    for period in sorted(grouped):
        games: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in grouped[period]:
            games[row["game_id"]].append(row)
        for game_id in sorted(games):
            state.update_game(games[game_id])
    return state, inputs


def _unscored_predictions(models: Any, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    matrices = {name: _matrix(rows, features) for name, features in FEATURE_SETS.items()}
    centers = {name: np.maximum(0.0, models.centers[name].predict(matrices[name])) for name in FEATURE_SETS}
    variances = {name: np.maximum(.05, np.expm1(models.variances[name].predict(matrices[name])))
                 for name in FEATURE_SETS}
    rich_x = matrices["rich"]
    direct = np.column_stack([models.quantiles[q].predict(rich_x) for q in QUANTILES])
    output = []
    for index, row in enumerate(rows):
        candidates = {"quantile_direct_rich": _non_crossing(direct[index])}
        means = {"quantile_direct_rich": float(centers["rich"][index])}
        for feature_set in FEATURE_SETS:
            for family in ("normal", "student_t", "lognormal", "gamma", "poisson", "negative_binomial",
                           "zero_inflated_poisson", "zero_inflated_negative_binomial"):
                name = f"parametric_{feature_set}_{family}"
                candidates[name] = _distribution_quantiles(family, float(centers[feature_set][index]),
                                                             float(variances[feature_set][index]),
                                                             float(row["features"].get("zero_rate") or 0))
                means[name] = float(centers[feature_set][index])
        output.append({**row, "candidate_quantiles": candidates, "candidate_means": means,
                       "direct_quantiles": _non_crossing(direct[index]),
                       "basic_variance": float(variances["basic"][index]),
                       "rich_variance": float(variances["rich"][index])})
    return output


def _raw_side_probabilities(row: dict[str, Any], configuration: str, line: float) -> tuple[float, float]:
    quantiles = row["candidate_quantiles"][configuration]
    if configuration == "quantile_direct_rich":
        under = _piecewise_cdf(line, quantiles)
        return 1 - under, under
    feature_set = "rich" if "_rich_" in configuration else "basic"
    family = configuration.split(f"parametric_{feature_set}_", 1)[1]
    over, under, _push = _family_probability(family, line, row["candidate_means"][configuration],
                                              row[f"{feature_set}_variance"],
                                              float(row["features"].get("zero_rate") or 0))
    return over, under


def _fit_residual_models(history: Sequence[dict[str, Any]], half_life: float = 9.0) -> dict[str, LogisticRegression]:
    periods = sorted({(int(row["season"]), int(row["week"])) for row in history})
    period_index = {period: index for index, period in enumerate(periods)}
    models = {}
    for market in LAUNCH_MARKETS:
        rows = [row for row in history if row["market"] == market and row["grade"] in {"WIN", "LOSS"}]
        x = np.asarray([[_logit(float(row["no_vig_market_probability"])),
                         float(row["model_probability"]) - float(row["no_vig_market_probability"])] for row in rows])
        y = np.asarray([row["grade"] == "WIN" for row in rows], dtype=int)
        weights = np.asarray([.5 ** ((len(periods) - 1 - period_index[(int(row["season"]), int(row["week"]))]) /
                                      half_life) for row in rows])
        models[market] = LogisticRegression(C=1.0, max_iter=2000, random_state=1729).fit(x, y, sample_weight=weights)
    return models


def _logit(value: float) -> float:
    clipped = min(1 - 1e-6, max(1e-6, value))
    return float(np.log(clipped / (1 - clipped)))


def generate_candidates(*, snapshot_root: Path, system_a_dir: Path, config_path: Path,
                        calibration_rows_path: Path, price_history_path: Path, games_path: Path,
                        quotes_path: Path, output_path: Path, generated_at: str) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["configuration_fingerprint"] != _digest({k: v for k, v in config.items()
                                                        if k != "configuration_fingerprint"}):
        raise ValueError("shadow prediction configuration fingerprint is invalid")
    games = json.loads(games_path.read_text(encoding="utf-8"))
    quotes = json.loads(quotes_path.read_text(encoding="utf-8"))
    pairs = prepare_quote_pairs(quotes, games, generated_at=generated_at)
    if not pairs:
        raise ValueError("no complete pregame quote pairs")
    if any(int(pair["season"]) <= max(int(value) for value in config["training_seasons"]) for pair in pairs):
        raise ValueError("shadow candidate season must follow every frozen training season")
    samples, training_inputs, _audit = build_distribution_samples(
        snapshot_root, config["training_seasons"], config["min_history"], system_a_dir=system_a_dir)
    state, state_inputs = _history_state(snapshot_root, system_a_dir, config["training_seasons"])
    game_index = {str(row["game_id"]): row for row in games}
    bases: dict[tuple[str, str, str], dict[str, Any]] = {}
    for pair in pairs:
        game = game_index[pair["game_id"]]
        team = pair["team"]; home = normalize_team(game.get("home_team")); away = normalize_team(game.get("away_team"))
        if team not in {home, away}:
            raise ValueError(f"quote team does not belong to game: {pair['base_key']}")
        opponent = away if team == home else home if team == away else "UNKNOWN"
        key = (pair["game_id"], pair["canonical_player_id"], pair["market"])
        base = {"season": pair["season"], "week": pair["week"], "game_id": pair["game_id"],
                "player_id": pair["canonical_player_id"], "canonical_player_id": pair["canonical_player_id"],
                "player_name": pair.get("player_name"), "team": team, "opponent": opponent,
                "home": 1.0 if team == home else 0.0 if team == away else None, "market": pair["market"]}
        base["features"] = state.features(base, pair["market"])
        if int(base["features"].get("history_games") or 0) < int(config["min_history"]):
            continue
        bases[key] = base
    predicted = {}
    seed = int(config["seed"])
    for market_index, market in enumerate(LAUNCH_MARKETS):
        train = [row for row in samples if row["market"] == market]
        test = [row for row in bases.values() if row["market"] == market]
        if not test:
            continue
        models = _fit_fold_models(train, seed + 100000 + market_index * 100)
        for row in _unscored_predictions(models, test):
            predicted[(row["game_id"], row["canonical_player_id"], market)] = row
    calibration = json.loads(calibration_rows_path.read_text(encoding="utf-8"))
    price_history = json.loads(price_history_path.read_text(encoding="utf-8"))
    residual_models = _fit_residual_models(price_history)
    output = []
    for pair in pairs:
        projection = predicted.get((pair["game_id"], pair["canonical_player_id"], pair["market"]))
        if projection is None:
            continue
        configuration = config["distribution_configuration_by_market"][pair["market"]]
        over_raw, under_raw = _raw_side_probabilities(projection, configuration, pair["line"])
        calibrated = {}
        for side, probability in (("OVER", over_raw), ("UNDER", under_raw)):
            historical = [row for row in calibration if row["market"] == pair["market"] and row["side"] == side
                          and row["result"] in {"WIN", "LOSS"}]
            method = config["calibration_by_market_side"][f"{pair['market']}:{side}"]["method"]
            calibrated[side] = float(_fit_predict(method, historical, [probability])[0])
        total = calibrated["OVER"] + calibrated["UNDER"]
        calibrated = {side: value / total for side, value in calibrated.items()}
        residual_raw = {}
        for side in ("OVER", "UNDER"):
            market_probability = pair[f"consensus_no_vig_{side.lower()}"]
            vector = np.asarray([[_logit(market_probability), calibrated[side] - market_probability]])
            residual_raw[side] = float(residual_models[pair["market"]].predict_proba(vector)[0, 1])
        residual_total = sum(residual_raw.values())
        for side in ("OVER", "UNDER"):
            quote = pair[f"best_{side.lower()}"]
            output.append({
                "season": pair["season"], "week": pair["week"], "game_id": pair["game_id"],
                "canonical_player_id": pair["canonical_player_id"], "player_name": pair.get("player_name"),
                "team": pair["team"], "market": pair["market"], "line": pair["line"], "side": side,
                "policy_probability": residual_raw[side] / residual_total, "push_probability": 0.0,
                "decimal_odds": float(quote["decimal_odds"]), "american_odds": quote.get("american_odds"),
                "bookmaker": quote.get("bookmaker") or quote.get("sportsbook"),
                "quote_timestamp": quote["quote_timestamp"], "generated_at": generated_at,
                "prediction_cutoff": quote["prediction_cutoff"],
                "probability_provenance": {"configuration_fingerprint": config["configuration_fingerprint"],
                    "distribution_configuration": configuration, "raw_distribution_probability": over_raw if side == "OVER" else under_raw,
                    "calibrated_probability": calibrated[side], "market_no_vig_probability": pair[f"consensus_no_vig_{side.lower()}"],
                    "residual_configuration": config["residual_configuration"]},
                "price_provenance": {"complete_books": pair["complete_books"], "best_price_book": quote.get("bookmaker") or quote.get("sportsbook")},
            })
    output.sort(key=lambda row: (row["season"], row["week"], row["game_id"], row["canonical_player_id"],
                                 row["market"], row["line"], row["side"]))
    _write_json(output_path, output)
    manifest = {
        "schema_version": 1, "configuration_fingerprint": config["configuration_fingerprint"],
        "generated_at": generated_at, "candidate_rows": len(output), "complete_bases": len(output) // 2,
        "inputs": {path.as_posix(): _file_hash(path) for path in sorted(set([
            config_path, calibration_rows_path, price_history_path, games_path, quotes_path,
            *training_inputs, *state_inputs]), key=lambda item: item.as_posix())},
        "output": {output_path.as_posix(): _file_hash(output_path)}, "network_contacted": False,
        "outcome_fields_present": False, "production_wagering_authorized": False,
    }
    _write_json(output_path.with_name(output_path.stem + "_manifest.json"), manifest)
    return {"candidates": output, "manifest": manifest}


def shadow_readiness(snapshot_root: Path, *, season: int) -> dict[str, Any]:
    season_dir = snapshot_root / "nfl" / str(season)
    weeks = []
    for directory in sorted(season_dir.glob("week_*")) if season_dir.exists() else []:
        games, quotes = directory / "games.json", directory / "player_prop_odds.json"
        weeks.append({"week_directory": directory.name, "games_present": games.exists(),
                      "quotes_present": quotes.exists(),
                      "ready_for_generation": games.exists() and quotes.exists()})
    ready = [row for row in weeks if row["ready_for_generation"]]
    return {"schema_version": 1, "season": season, "snapshot_root": snapshot_root.as_posix(),
            "weeks_discovered": len(weeks), "weeks_ready": len(ready), "weeks": weeks,
            "status": "READY_FOR_CANDIDATE_GENERATION" if ready else "WAITING_FOR_PREGAME_GAMES_AND_QUOTES",
            "network_contacted": False, "production_wagering_authorized": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze-config")
    freeze.add_argument("--experiment", type=Path, required=True); freeze.add_argument("--calibration-rows", type=Path, required=True)
    freeze.add_argument("--price-history", type=Path, required=True); freeze.add_argument("--shadow-policy", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    generate = sub.add_parser("generate")
    generate.add_argument("--snapshot-root", type=Path, required=True); generate.add_argument("--system-a-dir", type=Path, required=True)
    generate.add_argument("--config", dest="config_path", type=Path, required=True)
    generate.add_argument("--calibration-rows", dest="calibration_rows_path", type=Path, required=True)
    generate.add_argument("--price-history", dest="price_history_path", type=Path, required=True)
    generate.add_argument("--games", dest="games_path", type=Path, required=True)
    generate.add_argument("--quotes", dest="quotes_path", type=Path, required=True)
    generate.add_argument("--output", dest="output_path", type=Path, required=True); generate.add_argument("--generated-at", required=True)
    readiness = sub.add_parser("readiness"); readiness.add_argument("--snapshot-root", type=Path, required=True)
    readiness.add_argument("--season", type=int, required=True); readiness.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "freeze-config":
        freeze_prediction_config(experiment_path=args.experiment, calibration_rows_path=args.calibration_rows,
                                 price_history_path=args.price_history, shadow_policy_path=args.shadow_policy,
                                 output_path=args.output)
    elif args.command == "generate":
        generate_candidates(**{key: value for key, value in vars(args).items() if key != "command"})
    else:
        _write_json(args.output, shadow_readiness(args.snapshot_root, season=args.season))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
