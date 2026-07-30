"""Offline-only NFL V3 development and explicitly unlocked holdout evaluation."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from statistics import median
from typing import Any

from .config import SNAPSHOTS_DIR
from .evaluation import error_metrics, probability_metrics
from .game_matching import match_game, normalize_team
from .historical_provider import HistoricalSnapshotProvider
from .markets import normalize_market
from .nfl_game_predictor import (NFLGameMarketPredictor, V1_MODEL_VERSION,
                                 V2_MODEL_VERSION, no_vig_probabilities)
from .nfl_v3 import (NFLResearchSplit, NFLV3Config, V3_MODEL_VERSION,
                     chronological_folds, create_holdout_manifest,
                     verify_holdout_manifest)
from .outcomes import normalize_outcomes
from .snapshots import SnapshotError, snapshot_week_dir
from .team_history import (filter_market_quotes, market_quote_known_at, prediction_cutoff,
                           prediction_cutoff_source)

REQUIRED_EVALUATION_DATASETS = ("games", "outcomes", "team_stats", "odds")
SUPPORTED_MODELS = (V1_MODEL_VERSION, V2_MODEL_VERSION, V3_MODEL_VERSION)


def snapshot_hashes(root: Path, season: int, start: int, end: int) -> dict[str, str]:
    result = {}
    for week in range(start, end + 1):
        directory = snapshot_week_dir(root, "nfl", season, week)
        for path in sorted(directory.glob("*.json")):
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _require_week(directory: Path, season: int, week: int) -> None:
    if not directory.is_dir():
        raise SnapshotError(f"Missing week snapshot for NFL {season} Week {week}: {directory}")
    for name in REQUIRED_EVALUATION_DATASETS:
        path = directory / f"{name}.json"
        if not path.exists():
            raise SnapshotError(f"Missing {name} file for NFL {season} Week {week}: {path}")
        try:
            value = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise SnapshotError(f"Malformed {name} JSON for NFL {season} Week {week}: {path} ({exc})") from exc
        if not isinstance(value, list):
            raise SnapshotError(f"Malformed {name} snapshot for NFL {season} Week {week}: expected a list at {path}")
        if not value:
            raise SnapshotError(f"Empty required dataset {name} for NFL {season} Week {week}: {path}")


def _canonical_odds(games: list[dict[str, Any]], odds: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Attach provider odds to the authoritative games using replay identity rules."""
    canonical = {str(game.get("game_id")): game for game in games if game.get("game_id") is not None}
    aliases = {}
    for game_id, game in canonical.items():
        for field in ("provider_game_id", "event_id", "odds_event_id", "the_odds_api_event_id"):
            if game.get(field) not in (None, ""):
                aliases[str(game[field])] = game_id
    grouped: dict[str, list[dict[str, Any]]] = {game_id: [] for game_id in canonical}
    for row in odds:
        source_id = row.get("game_id") or row.get("event_id") or row.get("provider_game_id")
        game_id = str(source_id) if source_id is not None and str(source_id) in canonical else aliases.get(str(source_id))
        if game_id is None:
            matched = match_game(row, games, league="nfl")
            game_id = str(matched.game_id) if matched.matched else None
        if game_id in grouped:
            grouped[game_id].append({**row, "game_id": game_id, "market": normalize_market(row.get("market"))})
    return grouped


def _market_context(game: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Build cutoff-frozen, no-vig home-side context for V3."""
    frozen, _diagnostics = filter_market_quotes(game, rows)
    valid = [row for row in frozen if normalize_market(row.get("market")) == "h2h"
             and row.get("odds") not in (None, "", 0) and row.get("selection")]
    probabilities = []
    by_book: dict[str, list[dict[str, Any]]] = {}
    for row in valid:
        by_book.setdefault(str(row.get("sportsbook") or row.get("bookmaker") or ""), []).append(row)
    home = normalize_team(game.get("home_team"))
    for quotes in by_book.values():
        if len(quotes) != 2:
            continue
        try:
            probs = no_vig_probabilities([float(row["odds"]) for row in quotes])
        except (TypeError, ValueError):
            continue
        for row, probability in zip(quotes, probs):
            if normalize_team(row.get("selection")) == home:
                probabilities.append(probability)
    if not probabilities:
        return None
    captured = max(market_quote_known_at(row) for row in valid).isoformat().replace("+00:00", "Z")
    return {"moneyline_probability": median(probabilities), "captured_at": captured}


def _market_lines(game: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    home = normalize_team(game.get("home_team"))
    spread = [float(r["line"]) for r in rows if normalize_market(r.get("market")) == "spread"
              and r.get("line") is not None and normalize_team(r.get("selection")) == home]
    totals = [float(r["line"]) for r in rows if normalize_market(r.get("market")) == "total" and r.get("line") is not None]
    return (median(spread) if spread else None, median(totals) if totals else None)


def _summary(items: list[dict[str, Any]], start: int, end: int) -> dict[str, Any]:
    h2h = probability_metrics((r["probabilities"]["h2h"], r["outcome"]) for r in items
                              if r["outcome"] is not None)
    markets = {}
    for market in ("h2h", "spread", "total"):
        pairs = [(r["probabilities"][market], r["market_outcomes"][market]) for r in items
                 if r["probabilities"].get(market) is not None and r["market_outcomes"].get(market) is not None]
        markets[market] = probability_metrics(pairs)
    return {
        "games": len(items), "probability": h2h, "calibration": {
            "ece": h2h["calibration_error"], "buckets": h2h["calibration_buckets"]},
        "home_error": error_metrics((r["projected_home"], r["actual_home"]) for r in items),
        "away_error": error_metrics((r["projected_away"], r["actual_away"]) for r in items),
        "margin_error": error_metrics((r["projected_home"] - r["projected_away"], r["actual_home"] - r["actual_away"]) for r in items),
        "total_error": error_metrics((r["projected_home"] + r["projected_away"], r["actual_home"] + r["actual_away"]) for r in items),
        "weekly": {str(w): {"games": len(week_rows), "probability": probability_metrics(
            (r["probabilities"]["h2h"], r["outcome"]) for r in week_rows if r["outcome"] is not None),
            "margin_error": error_metrics((r["projected_home"] - r["projected_away"], r["actual_home"] - r["actual_away"]) for r in week_rows),
            "total_error": error_metrics((r["projected_home"] + r["projected_away"], r["actual_home"] + r["actual_away"]) for r in week_rows)}
            for w in range(start, end + 1) for week_rows in [[r for r in items if r["week"] == w]]},
        "markets": markets,
    }


def evaluate(root: Path, season: int, start: int, end: int, models: list[str], config: NFLV3Config,
             label: str) -> dict[str, Any]:
    if start > end:
        raise ValueError("start week must not exceed end week")
    if not models or len(models) != len(set(models)) or any(model not in SUPPORTED_MODELS for model in models):
        raise ValueError(f"models must be unique supported versions: {SUPPORTED_MODELS}")
    provider = HistoricalSnapshotProvider(root)
    rows = {model: [] for model in models}
    exclusions: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {"snapshots_discovered": 0, "weeks_discovered": [], "games_loaded": 0,
        "games_with_complete_outcomes": 0, "games_with_usable_historical_features": 0,
        "cutoff_sources": {"prediction_cutoff": 0, "prediction_timestamp": 0, "kickoff_fallback": 0},
        "future_market_rows_rejected": 0,
        "predictions_generated_per_model": {model: 0 for model in models},
        "games_evaluated_per_model": {model: 0 for model in models}}
    for week in range(start, end + 1):
        directory = snapshot_week_dir(root, "nfl", season, week)
        _require_week(directory, season, week)
        diagnostics["snapshots_discovered"] += 1
        diagnostics["weeks_discovered"].append(week)
        games = provider.get_games("nfl", str(season), week)
        outcomes = normalize_outcomes(provider.get_outcomes("nfl", str(season), week), games, "nfl", str(season), week)
        outcome_map = {str(o["game_id"]): o for o in outcomes if o.get("match_success")}
        odds_by_game = _canonical_odds(games, provider.get_odds("nfl", str(season), week))
        diagnostics["games_loaded"] += len(games)
        for game in games:
            game_id = str(game.get("game_id"))
            cutoff_source = prediction_cutoff_source(game)
            diagnostics["cutoff_sources"][cutoff_source] = diagnostics["cutoff_sources"].get(cutoff_source, 0) + 1
            outcome = outcome_map.get(game_id)
            if not outcome or not outcome.get("completed") or outcome.get("final_home_score") is None or outcome.get("final_away_score") is None:
                exclusions.append({"week": week, "game_id": game_id, "reason": "incomplete_or_unmatched_outcome"})
                continue
            diagnostics["games_with_complete_outcomes"] += 1
            projections = {}
            game_odds = odds_by_game.get(game_id, [])
            frozen_odds, market_diagnostic = filter_market_quotes(game, game_odds)
            diagnostics["future_market_rows_rejected"] += market_diagnostic["rejected_future"]
            context = _market_context(game, frozen_odds)
            views = provider.get_game_histories("nfl", str(season), week, game)
            for model in models:
                predictor = NFLGameMarketPredictor(model, config if model == V3_MODEL_VERSION else None)
                histories = views.target_team_history.rows if model == V1_MODEL_VERSION else views.league_team_history.rows
                projections[model] = predictor.project(game, histories, context) if model == V3_MODEL_VERSION else predictor.project(game, histories)
                if projections[model] is None:
                    exclusions.append({"week": week, "game_id": game_id, "model": model,
                                       "reason": "insufficient_pregame_history"})
                    continue
                diagnostics["predictions_generated_per_model"][model] += 1
                projection = projections[model]
                home, away = str(game["home_team"]), str(game["away_team"])
                actual_home, actual_away = float(outcome["final_home_score"]), float(outcome["final_away_score"])
                spread, total = _market_lines(game, frozen_odds)
                probabilities = {"h2h": projection.probability("h2h", home, home_team=home, away_team=away),
                    "spread": projection.probability("spread", home, spread, home_team=home, away_team=away) if spread is not None else None,
                    "total": projection.probability("total", "over", total, home_team=home, away_team=away) if total is not None else None}
                market_outcomes = {"h2h": int(actual_home > actual_away) if actual_home != actual_away else None,
                    "spread": int(actual_home - actual_away + spread > 0) if spread is not None and actual_home - actual_away + spread != 0 else None,
                    "total": int(actual_home + actual_away > total) if total is not None and actual_home + actual_away != total else None}
                rows[model].append({"week": week, "game_id": game_id,
                    "kickoff": game.get("kickoff_time") or game.get("commence_time"), "probabilities": probabilities,
                    "market_outcomes": market_outcomes,
                    "outcome": int(actual_home > actual_away) if actual_home != actual_away else None,
                    "projected_home": projection.home_points, "projected_away": projection.away_points,
                    "actual_home": actual_home, "actual_away": actual_away,
                    "cutoff_diagnostics": {"prediction_cutoff": prediction_cutoff(game).isoformat().replace("+00:00", "Z"),
                        "cutoff_source": prediction_cutoff_source(game),
                        "latest_team_feature_timestamp": views.league_team_history.latest_timestamp,
                        "latest_player_feature_timestamp": views.player_history.latest_timestamp,
                        "latest_market_snapshot_timestamp": market_diagnostic["latest_timestamp"],
                        "eligible_market_quote_ids": [str(q.get("quote_id") or q.get("id") or q.get("snapshot_timestamp") or q.get("captured_at")) for q in frozen_odds],
                        "future_market_rows_rejected": market_diagnostic["rejected_future"]},
                    "feature_diagnostics": getattr(predictor, "last_feature_diagnostics", {}),
                    "v3_probabilities": ({key: projection.features.get(key) for key in
                        ("football_probability", "market_probability", "blended_probability")}
                        if model == V3_MODEL_VERSION else None)})
            if all(projections.get(model) is not None for model in models):
                diagnostics["games_with_usable_historical_features"] += 1
    for items in rows.values():
        items.sort(key=lambda r: (str(r.get("kickoff") or ""), r["game_id"]))
    universes = {model: {(r["week"], r["game_id"]) for r in items} for model, items in rows.items()}
    if len({frozenset(value) for value in universes.values()}) != 1:
        raise ValueError(f"models produced different eligible game universes: {universes}")
    eligible = next(iter(universes.values()), set())
    if not eligible:
        reasons = Counter(item["reason"] for item in exclusions)
        raise ValueError(f"zero eligible games for NFL {season} Weeks {start}-{end}; exclusion_reasons={dict(sorted(reasons.items()))}")
    diagnostics["games_evaluated_per_model"] = {model: len(items) for model, items in rows.items()}
    diagnostics["excluded_games"] = len({(item["week"], item["game_id"]) for item in exclusions})
    diagnostics["exclusions"] = sorted(exclusions, key=lambda item: (item["week"], item["game_id"], item.get("model", ""), item["reason"]))
    diagnostics["exclusion_reason_counts"] = dict(sorted(Counter(item["reason"] for item in exclusions).items()))
    return {"result_type": label, "season": season, "evaluation_window": {"start_week": start, "end_week": end},
        "models": {model: _summary(items, start, end) for model, items in rows.items()}, "rows": rows,
        "eligibility": diagnostics, "configuration": asdict(config), "configuration_hash": config.configuration_hash,
        "chronological_folds": chronological_folds(range(start, end + 1)),
        "optimization_objectives": ["brier", "log_loss", "margin_mae", "total_mae"],
        "roi_role": "secondary_diagnostic_only"}


def markdown(report: dict[str, Any]) -> str:
    lines = [f"# {report['result_type']}", "", f"Evaluation window: Weeks {report['evaluation_window']['start_week']}–{report['evaluation_window']['end_week']}", "",
        "| Model | Games | Brier | Log loss | ECE | Margin MAE | Total MAE |", "|---|---:|---:|---:|---:|---:|---:|"]
    for model, summary in report["models"].items():
        lines.append(f"| {model} | {summary['games']} | {summary['probability']['brier']} | {summary['probability']['log_loss']} | {summary['calibration']['ece']} | {summary['margin_error']['mae']} | {summary['total_error']['mae']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", type=Path, default=SNAPSHOTS_DIR)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--development-start-week", type=int, default=1)
    parser.add_argument("--development-end-week", type=int, default=6)
    parser.add_argument("--holdout-start-week", type=int, default=7)
    parser.add_argument("--holdout-end-week", type=int, default=18)
    parser.add_argument("--models", default=f"{V1_MODEL_VERSION},{V2_MODEL_VERSION},{V3_MODEL_VERSION}")
    parser.add_argument("--output", type=Path); parser.add_argument("--markdown", type=Path); parser.add_argument("--config", type=Path)
    parser.add_argument("--freeze-holdout", action="store_true"); parser.add_argument("--evaluate-holdout", action="store_true"); parser.add_argument("--frozen-config", type=Path)
    args = parser.parse_args(argv)
    config = NFLV3Config(**json.loads(args.config.read_text())) if args.config else NFLV3Config()
    split = NFLResearchSplit(args.development_start_week, args.development_end_week, args.holdout_start_week)
    if args.freeze_holdout:
        if not args.frozen_config: parser.error("--freeze-holdout requires --frozen-config manifest path")
        create_holdout_manifest(args.frozen_config, args.season, split, config,
                                snapshot_hashes(args.snapshot_root, args.season, args.holdout_start_week, args.holdout_end_week)); return 0
    if args.evaluate_holdout:
        if not args.frozen_config: parser.error("--evaluate-holdout requires --frozen-config")
        hashes = snapshot_hashes(args.snapshot_root, args.season, args.holdout_start_week, args.holdout_end_week)
        verify_holdout_manifest(args.frozen_config, config, hashes)
        start, end, label = args.holdout_start_week, args.holdout_end_week, "HOLDOUT RESULT"
    else:
        start, end, label = args.development_start_week, args.development_end_week, "DEVELOPMENT RESULT"
        split.assert_tuning_weeks(range(start, end + 1))
    report = evaluate(args.snapshot_root, args.season, start, end, [m.strip() for m in args.models.split(",") if m.strip()], config, label)
    if args.output: args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.markdown: args.markdown.parent.mkdir(parents=True, exist_ok=True); args.markdown.write_text(markdown(report))
    if not args.output: print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
