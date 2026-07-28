"""Offline multi-week snapshot and replay readiness check."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .config import BacktestConfig, SNAPSHOTS_DIR
from .outcomes import game_id, normalize_outcomes
from .replay_engine import ReplayEngine
from .snapshots import SnapshotError, _parse_iso, validate_snapshot


def validate_multiweek_replay(league: str, season: str, start_week: int, end_week: int, data_dir: Path = SNAPSHOTS_DIR, *, run_replay: bool = True) -> dict:
    """Validate identities, time ordering, duplicates, grading, and totals offline."""
    weeks = list(range(start_week, end_week + 1))
    snapshot = validate_snapshot(data_dir, league, season, weeks)
    errors, warnings = list(snapshot.errors), list(snapshot.warnings)
    identities: set[tuple[str, str, int, str]] = set()
    counts = {"weeks_processed": 0, "games": 0, "outcomes": 0, "odds": 0}
    provider = __import__("backtesting.historical_provider", fromlist=["HistoricalSnapshotProvider"]).HistoricalSnapshotProvider(data_dir)
    for week in weeks:
        try:
            games = provider.get_games(league, season, week)
            odds = provider.get_odds(league, season, week)
            finals = normalize_outcomes(provider.get_outcomes(league, season, week), games, league, season, week)
        except SnapshotError as exc:
            errors.append(str(exc)); continue
        counts["weeks_processed"] += 1; counts["games"] += len(games); counts["outcomes"] += len(finals); counts["odds"] += len(odds)
        game_ids = {game_id(row) for row in games}
        for row in games:
            key = (league.lower(), str(season), week, str(game_id(row)))
            if key in identities: errors.append(f"duplicate_game_identity: {key}")
            identities.add(key)
        for row in finals:
            if game_id(row) not in game_ids: errors.append(f"unmatched_outcome: week={week} game={game_id(row)}")
        kickoff = {game_id(row): _parse_iso(row.get("kickoff_time")) for row in games}
        seen_odds = set()
        for row in odds:
            identity = json.dumps(row, sort_keys=True, default=str)
            if identity in seen_odds: errors.append(f"duplicate_odds: week={week} game={game_id(row)}")
            seen_odds.add(identity)
            captured = _parse_iso(row.get("captured_at") or row.get("snapshot_timestamp") or row.get("timestamp"))
            if game_id(row) not in game_ids: errors.append(f"wrong_game_odds: week={week} game={game_id(row)}")
            elif captured and kickoff.get(game_id(row)) and captured >= kickoff[game_id(row)]: errors.append(f"post_kickoff_odds: week={week} game={game_id(row)}")
    replay = None
    if run_replay and not errors:
        with tempfile.TemporaryDirectory() as temp:
            config = BacktestConfig(league=league, season=season, start_week=start_week, end_week=end_week,
                data_dir=data_dir, db_path=Path(temp) / "readiness.db", export=False)
            with ReplayEngine(config) as engine: replay = engine.run()
        if replay["metrics"]["ungraded_predictions"]:
            errors.append(f"incomplete_grading: {replay['metrics']['ungraded_predictions']} predictions")
        if replay["metrics"]["total_predictions"] != replay["evaluation"]["totals"]["bets_accepted"]:
            errors.append("aggregation_mismatch: stored predictions != accepted bets")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "counts": counts,
            "metrics": replay and replay["metrics"], "evaluation": replay and replay["evaluation"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", required=True); parser.add_argument("--season", required=True)
    parser.add_argument("--start-week", required=True, type=int); parser.add_argument("--end-week", required=True, type=int)
    parser.add_argument("--data-dir", type=Path, default=SNAPSHOTS_DIR); parser.add_argument("--no-replay", action="store_true")
    args = parser.parse_args()
    report = validate_multiweek_replay(args.league, args.season, args.start_week, args.end_week, args.data_dir, run_replay=not args.no_replay)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__": main()
