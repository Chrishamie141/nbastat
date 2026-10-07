"""Offline command for the NBA chronological replay foundation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.services.nba.replay import NBAReplayEngine


def _load(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"Expected a JSON list: {path}")
    return value


def replay_dataset(*, dataset_dir: Path, season: str, model_version: str, output: Path,
                   persist: bool = True) -> dict:
    games = _load(dataset_dir / "games.json")
    history = _load(dataset_dir / "team_history.json")
    outcomes = _load(dataset_dir / "outcomes.json")
    markets_path = dataset_dir / "markets.json"
    markets = _load(markets_path) if markets_path.exists() else []
    frozen_path = dataset_dir / "frozen_predictions.json"
    frozen = _load(frozen_path) if frozen_path.exists() else []
    by_game: dict[str, list[dict]] = {}
    for row in frozen:
        by_game.setdefault(str(row["game_id"]), []).append(row)

    def prediction_factory(game, _history, version):
        rows = by_game.get(str(game["game_id"]), [])
        for row in rows:
            if row.get("model_version") not in {None, version}:
                raise ValueError(f"Frozen NBA model version mismatch for {game['game_id']}")
        return rows

    summary = NBAReplayEngine(season=season, model_version=model_version,
                              prediction_factory=prediction_factory).run(
        games=games, history=history, outcomes=outcomes, markets=markets)
    if persist:
        from backend.app.services.nba.lifecycle import persist_replay_summary
        summary["runId"] = persist_replay_summary(summary, config={
            "datasetDir": str(dataset_dir), "season": season, "modelVersion": model_version,
            "sourceFiles": sorted(path.name for path in dataset_dir.glob("*.json")),
        })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an offline chronological NBA replay")
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--model-version", default="nba-player-stat-v1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    summary = replay_dataset(dataset_dir=args.dataset_dir, season=args.season,
                             model_version=args.model_version, output=args.output)
    print(json.dumps(summary["metrics"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
