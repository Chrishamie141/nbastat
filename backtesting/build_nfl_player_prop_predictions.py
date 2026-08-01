"""Build frozen, offline NFL player-prop probabilities from historical snapshots.

This command deliberately has no provider imports.  Quotes define only the
opportunities to price; grades and realized outcomes are never inputs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from .config import SNAPSHOTS_DIR
from .game_matching import parse_dt
from .historical_provider import HistoricalSnapshotProvider
from .markets import CANONICAL_PLAYER_PROP_MARKETS
from .nfl_game_predictor import (NFLGameMarketPredictor, V1_MODEL_VERSION,
                                 V2_MODEL_VERSION, V3_MODEL_VERSION)
from .nfl_simulation import NFLGameSimulator, PLAYER_MARKETS
from .nfl_v3 import NFLV3Config
from .player_identity import first_player_id, normalize_player_id
from .snapshots import snapshot_week_dir
from .team_history import prediction_cutoff, prediction_cutoff_source

MODEL_VERSIONS = (V1_MODEL_VERSION, V2_MODEL_VERSION, V3_MODEL_VERSION)
READINESS = (
    "READY", "NOT_READY_INSUFFICIENT_HISTORY", "NOT_READY_NO_PLAYER_DATA",
    "NOT_READY_UNSUPPORTED_MARKET", "NOT_READY_IDENTITY_ONLY",
    "NOT_READY_MISSING_TEAM_CONTEXT",
)


def _load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, value: Any) -> None:
    content = json.dumps(value, indent=2, sort_keys=True) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def opportunity_key(row: dict[str, Any]) -> tuple[int, int, str, str, str, float, str] | None:
    """Return the bookmaker-independent canonical prediction identity."""
    player_id = first_player_id(row.get("canonical_player_id"), row.get("player_id"))
    side = str(row.get("selection") or row.get("side") or "").upper()
    try:
        line = float(row["line"])
        season, week = int(row["season"]), int(row["week"])
    except (KeyError, TypeError, ValueError):
        return None
    if not player_id or str(player_id).upper() == "UNKNOWN" or side not in {"OVER", "UNDER"}:
        return None
    return season, week, str(row.get("game_id")), str(player_id), str(row.get("market")), line, side


def collapse_opportunities(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse identical lines across books without inspecting outcome fields."""
    selected: dict[tuple[Any, ...], dict[str, Any]] = {}
    safe_fields = ("season", "week", "game_id", "canonical_player_id", "player_id",
                   "player_name", "canonical_player_name", "team", "market", "line",
                   "selection", "side")
    for raw in rows:
        row = {field: raw.get(field) for field in safe_fields}
        key = opportunity_key(row)
        if key is not None and key not in selected:
            selected[key] = row
    return [selected[key] for key in sorted(selected)]


def probabilities(values: Any, line: float) -> dict[str, float]:
    """Reuse simulation draws to preserve discrete pushes exactly."""
    return {"OVER": float((values > line).mean()), "UNDER": float((values < line).mean()),
            "PUSH": float((values == line).mean())}


def _logical_timestamp(game: dict[str, Any]) -> str:
    cutoff = prediction_cutoff(game)
    if cutoff is None:
        raise ValueError(f"game {game.get('game_id')} has no prediction cutoff")
    return cutoff.isoformat().replace("+00:00", "Z")


def _game_seed(seed: int, season: int, week: int, game_id: str) -> int:
    digest = hashlib.sha256(f"{season}|{week}|{game_id}".encode()).digest()
    return (int(seed) + int.from_bytes(digest[:4], "big")) % (2**32)


def _identity_index(directory: Path) -> dict[str, dict[str, Any]]:
    return {player_id: row
            for row in _load(directory / "player_identities.json", [])
            if (player_id := normalize_player_id(row.get("canonical_player_id"))) is not None}


def _not_ready(row: dict[str, Any], game: dict[str, Any], reason: str, *, model_version: str,
               seed: int, simulations: int, provenance: dict[str, Any]) -> dict[str, Any]:
    key = opportunity_key(row)
    assert key is not None
    return {"season": key[0], "week": key[1], "game_id": key[2],
            "canonical_player_id": key[3],
            "player_name": row.get("player_name") or row.get("canonical_player_name"),
            "team": row.get("team"), "market": key[4], "line": key[5], "side": key[6],
            "model_probability": None, "push_probability": None,
            "model_version": model_version, "prediction_cutoff": _logical_timestamp(game),
            "generated_at": _logical_timestamp(game), "seed": seed, "simulations": simulations,
            "readiness": reason, "provenance": provenance}


def build_week(provider: HistoricalSnapshotProvider, root: Path, season: int, week: int,
               model_version: str, simulations: int, seed: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    directory = snapshot_week_dir(root, "nfl", season, week)
    quotes = provider.get_player_prop_odds("nfl", str(season), week)
    opportunities = collapse_opportunities(quotes)
    games = {str(game.get("game_id")): game for game in provider.get_games("nfl", str(season), week)}
    by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in opportunities:
        by_game[str(row.get("game_id"))].append(row)
    identities = _identity_index(directory)
    output: list[dict[str, Any]] = []
    distributions = 0
    players_considered: set[tuple[str, str]] = set()

    for game_id in sorted(by_game):
        game = games.get(game_id)
        if game is None:
            raise ValueError(f"player-prop quote references unknown game {game_id}")
        views = provider.get_game_histories("nfl", str(season), week, game)
        league_history = views.league_team_history.rows
        team_history = views.target_team_history.rows
        player_history = views.player_history.rows
        cutoff = _logical_timestamp(game)
        base_provenance = {
            "cutoff_source": prediction_cutoff_source(game),
            "team_history_rows": len(team_history), "league_team_history_rows": len(league_history),
            "player_history_rows": len(player_history),
            "latest_team_history_timestamp": views.target_team_history.latest_timestamp,
            "latest_player_history_timestamp": views.player_history.latest_timestamp,
            "outcome_inputs": [], "network_contacted": False,
        }
        game_seed = _game_seed(seed, season, week, game_id)
        supported = [row for row in by_game[game_id] if str(row.get("market")) in PLAYER_MARKETS]
        predictor_history = team_history if model_version == V1_MODEL_VERSION else league_history
        projection = None
        if predictor_history:
            predictor = NFLGameMarketPredictor(model_version, NFLV3Config() if model_version == V3_MODEL_VERSION else None)
            projection = predictor.project(game, predictor_history, None) if model_version == V3_MODEL_VERSION else predictor.project(game, predictor_history)
        result = None
        if projection is not None and supported:
            result = NFLGameSimulator().simulate(game, team_history, player_history, None,
                                                 model_version, simulations, game_seed, projection)
        history_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in player_history:
            pid = normalize_player_id(item.get("player_id"))
            if pid:
                history_by_id[pid].append(item)

        counted: set[tuple[str, str]] = set()
        for row in by_game[game_id]:
            key = opportunity_key(row)
            assert key is not None
            pid, market = key[3], key[4]
            players_considered.add((game_id, pid))
            provenance = dict(base_provenance)
            provenance["player_history_games"] = len(history_by_id.get(normalize_player_id(pid) or "", []))
            if market not in CANONICAL_PLAYER_PROP_MARKETS or market not in PLAYER_MARKETS:
                output.append(_not_ready(row, game, "NOT_READY_UNSUPPORTED_MARKET", model_version=model_version,
                                         seed=seed, simulations=simulations, provenance=provenance)); continue
            if projection is None:
                output.append(_not_ready(row, game, "NOT_READY_MISSING_TEAM_CONTEXT", model_version=model_version,
                                         seed=seed, simulations=simulations, provenance=provenance)); continue
            history = history_by_id.get(normalize_player_id(pid) or "", [])
            if not history:
                identity = identities.get(pid, {})
                reason = "NOT_READY_IDENTITY_ONLY" if identity and not identity.get("has_stats", False) else "NOT_READY_NO_PLAYER_DATA"
                output.append(_not_ready(row, game, reason, model_version=model_version, seed=seed,
                                         simulations=simulations, provenance=provenance)); continue
            names = [str(item.get("player_name")) for item in history if item.get("player_name")]
            player_name = names[-1] if names else str(row.get("player_name") or "")
            values = result.player_outcomes.get((player_name, market)) if result is not None else None
            if values is None:
                output.append(_not_ready(row, game, "NOT_READY_INSUFFICIENT_HISTORY", model_version=model_version,
                                         seed=seed, simulations=simulations, provenance=provenance)); continue
            if (pid, market) not in counted:
                counted.add((pid, market)); distributions += 1
            probs = probabilities(values, key[5])
            output.append({"season": season, "week": week, "game_id": game_id,
                "canonical_player_id": pid, "player_name": row.get("player_name") or player_name,
                "team": row.get("team") or history[-1].get("team"), "market": market,
                "line": key[5], "side": key[6], "model_probability": probs[key[6]],
                "over_probability": probs["OVER"], "under_probability": probs["UNDER"],
                "push_probability": probs["PUSH"], "model_version": model_version,
                "prediction_cutoff": cutoff, "generated_at": cutoff, "seed": seed,
                "simulation_seed": game_seed, "simulations": simulations, "readiness": "READY",
                "provenance": provenance})

    output.sort(key=lambda row: (row["season"], row["week"], row["game_id"],
                                 row["canonical_player_id"], row["market"], row["line"], row["side"]))
    readiness = Counter(row["readiness"] for row in output)
    def readiness_by(field: str) -> dict[str, dict[str, int]]:
        grouped: dict[str, Counter[str]] = defaultdict(Counter)
        for item in output:
            grouped[str(item.get(field))][item["readiness"]] += 1
        return {key: dict(sorted(counts.items())) for key, counts in sorted(grouped.items())}
    diagnostics = {"games_considered": len(by_game), "players_considered": len(players_considered),
        "historical_quote_rows": len(quotes), "unique_opportunities": len(opportunities),
        "unique_player_market_distributions_simulated": distributions,
        "ready_predictions": readiness["READY"],
        "not_ready_predictions": len(output) - readiness["READY"],
        "readiness_counts": dict(sorted(readiness.items())),
        "readiness_by_market": readiness_by("market"),
        "readiness_by_game": readiness_by("game_id"),
        "readiness_by_player": readiness_by("canonical_player_id"),
        "readiness_by_week": readiness_by("week"),
        "predictions_by_market": dict(sorted(Counter(row["market"] for row in output).items()))}
    return output, diagnostics


def persist_week(root: Path, season: int, week: int, rows: list[dict[str, Any]],
                 diagnostics: dict[str, Any], *, model_version: str, seed: int,
                 simulations: int, overwrite: bool) -> None:
    directory = snapshot_week_dir(root, "nfl", season, week)
    target = directory / "player_prop_predictions.json"
    if target.exists() and not overwrite:
        raise FileExistsError(f"{target} exists; pass --overwrite to replace it")
    _atomic_json(target, rows)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest_path = directory / "manifest.json"
    manifest = _load(manifest_path, {"datasets": {}})
    timestamp = max((str(row["generated_at"]) for row in rows), default=None)
    entry = {"present": True, "status": "complete" if rows else "optional_empty",
             "records": len(rows), "row_count": len(rows), "sha256": digest,
             "source": "offline-frozen-nfl-simulation", "model_version": model_version,
             "seed": seed, "simulations": simulations, "generation_timestamp": timestamp,
             "readiness_counts": diagnostics["readiness_counts"],
             "readiness_by_market": diagnostics.get("readiness_by_market", {}),
             "readiness_by_game": diagnostics.get("readiness_by_game", {}),
             "readiness_by_player": diagnostics.get("readiness_by_player", {}),
             "readiness_by_week": diagnostics.get("readiness_by_week", {})}
    manifest.setdefault("datasets", {})["player_prop_predictions"] = entry
    manifest.setdefault("source_versions", {})["player_prop_predictions"] = model_version
    manifest.setdefault("source_lineage", {})["player_prop_predictions"] = {
        "provider": "offline", "records": len(rows), "network_contacted": False,
        "inputs": ["games", "team_stats", "player_stats", "player_prop_odds"]}
    _atomic_json(manifest_path, manifest)


def build(root: Path, season: int, start_week: int, end_week: int, model_version: str,
          simulations: int, seed: int, *, validate: bool, overwrite: bool) -> dict[str, Any]:
    if simulations <= 0:
        raise ValueError("simulations must be positive")
    if start_week > end_week:
        raise ValueError("start-week must not exceed end-week")
    provider = HistoricalSnapshotProvider(root)
    totals = Counter(); weekly = {}; started = perf_counter()
    for week in range(start_week, end_week + 1):
        rows, diagnostics = build_week(provider, root, season, week, model_version, simulations, seed)
        if validate:
            for row in rows:
                if row["readiness"] == "READY":
                    total = row["over_probability"] + row["under_probability"] + row["push_probability"]
                    if abs(total - 1.0) > 1e-12:
                        raise ValueError(f"probabilities do not sum to one: {row}")
                if parse_dt(row["generated_at"]) != parse_dt(row["prediction_cutoff"]):
                    raise ValueError("frozen generation timestamp differs from prediction cutoff")
        persist_week(root, season, week, rows, diagnostics, model_version=model_version,
                     seed=seed, simulations=simulations, overwrite=overwrite)
        weekly[week] = diagnostics
        for key, value in diagnostics.items():
            if isinstance(value, int): totals[key] += value
    return {**dict(totals), "weeks": weekly, "runtime_seconds": perf_counter() - started,
            "network_contacted": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--start-week", type=int, default=1)
    parser.add_argument("--end-week", type=int, default=1)
    parser.add_argument("--snapshot-root", type=Path, default=SNAPSHOTS_DIR)
    parser.add_argument("--model-version", choices=MODEL_VERSIONS, default=V1_MODEL_VERSION)
    parser.add_argument("--simulations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    report = build(args.snapshot_root, args.season, args.start_week, args.end_week,
                   args.model_version, args.simulations, args.seed,
                   validate=args.validate, overwrite=args.overwrite)
    for label, key in (("games considered", "games_considered"),
                       ("players considered", "players_considered"),
                       ("historical quote rows", "historical_quote_rows"),
                       ("unique opportunities", "unique_opportunities"),
                       ("unique player-market distributions simulated", "unique_player_market_distributions_simulated"),
                       ("READY predictions", "ready_predictions"),
                       ("NOT_READY predictions", "not_ready_predictions")):
        print(f"{label}: {report.get(key, 0)}")
    markets = Counter()
    for week in report["weeks"].values(): markets.update(week["predictions_by_market"])
    print("predictions by market: " + json.dumps(dict(sorted(markets.items())), sort_keys=True))
    print(f"runtime: {report['runtime_seconds']:.3f}s")
    print("network_contacted=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
