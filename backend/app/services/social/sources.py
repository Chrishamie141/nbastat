"""Build signed-social input from immutable, server-side NFL evidence.

This module is deliberately read-only with respect to sports tables.  It emits
the same small, whitelisted document accepted by the social engine and leaves
prediction, market, result, and grading records untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable


def _json(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _american_probability(price: float) -> float:
    return -price / (-price + 100) if price < 0 else 100 / (price + 100)


def _prediction_market(payload: dict[str, Any], game: Any, sha: Callable) -> dict[str, Any] | None:
    market = payload.get("market") or {}
    observed = _dt(market.get("marketTimestamp"))
    kickoff = _dt(game["kickoff"])
    if not observed or not kickoff or observed >= kickoff:
        return None
    winner = payload.get("winner")
    home_probability = market.get("homeImpliedProbability")
    away_probability = market.get("awayImpliedProbability")
    if winner == game["home_team"]:
        probability = home_probability
    elif winner == game["away_team"]:
        probability = away_probability
    else:
        return None
    if probability is None:
        return None
    snapshot = {
        "provider": market.get("provider"), "bookmaker": market.get("sportsbook"),
        "market_timestamp": market.get("marketTimestamp"),
        "home_probability": home_probability, "away_probability": away_probability,
    }
    underdog = game["home_team"] if float(home_probability or 1) < float(away_probability or 1) else game["away_team"]
    return {
        "snapshot_id": sha(snapshot), "bookmaker": market.get("sportsbook"),
        "source_time": market.get("marketTimestamp"), "probability": float(probability),
        "edge": float(payload.get("winProbability")) - float(probability),
        "movement": None, "underdog": underdog,
    }


def _captured_markets(connection, experiment_key: str, games: dict[str, Any],
                      predictions: dict[str, dict[str, Any]], sha: Callable) -> dict[str, dict[str, Any]]:
    rows = connection.execute("""SELECT m.capture_key,m.game_id,m.bookmaker,m.source_time,m.source_epoch,
        m.retrieved_epoch,m.cutoff_epoch,q.side,q.price
        FROM nfl_capture_markets m JOIN nfl_capture_quotes q ON q.capture_key=m.capture_key
        WHERE m.experiment_key=? AND m.market_type='h2h' AND m.retrieved_epoch<m.cutoff_epoch
        AND m.source_epoch<m.cutoff_epoch ORDER BY m.game_id,m.source_epoch,m.retrieved_epoch,m.capture_key""",
        (experiment_key,)).fetchall()
    captures: dict[str, dict[str, Any]] = {}
    for row in rows:
        capture = captures.setdefault(row["capture_key"], {
            "game_id": row["game_id"], "bookmaker": row["bookmaker"], "source_time": row["source_time"],
            "source_epoch": row["source_epoch"], "prices": {},
        })
        capture["prices"][str(row["side"])] = float(row["price"])
    by_game: dict[str, list[dict[str, Any]]] = {}
    for capture in captures.values():
        game = games.get(capture["game_id"])
        prediction = predictions.get(capture["game_id"])
        if not game or not prediction or len(capture["prices"]) < 2:
            continue
        kickoff = _dt(game["kickoff"])
        observed = _dt(capture["source_time"])
        if not kickoff or not observed or observed >= kickoff:
            continue
        raw = {side: _american_probability(price) for side, price in capture["prices"].items()}
        total = sum(raw.values())
        if total <= 0 or prediction["winner"] not in raw:
            continue
        capture["probabilities"] = {side: value / total for side, value in raw.items()}
        by_game.setdefault(capture["game_id"], []).append(capture)
    result: dict[str, dict[str, Any]] = {}
    for game_id, values in by_game.items():
        first, latest = values[0], values[-1]
        winner = predictions[game_id]["winner"]
        probability = latest["probabilities"][winner]
        result[game_id] = {
            "snapshot_id": sha({"capture_key": next(key for key, value in captures.items() if value is latest)}),
            "bookmaker": latest["bookmaker"], "source_time": latest["source_time"],
            "probability": probability, "edge": predictions[game_id]["probability"] - probability,
            "movement": probability - first["probabilities"][winner] if len(values) > 1 else None,
            "underdog": min(latest["probabilities"], key=latest["probabilities"].get),
        }
    return result


def build_server_source(connection, sha: Callable, clock: Callable[[], datetime],
                        expected_week3_hash: str,
                        preseason_fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read current canonical evidence and return a private-data-free source."""
    week3 = connection.execute("""SELECT game_id,prediction_json,generated_at,kickoff_time
        FROM nfl_game_predictions WHERE user_id=0 AND season=2026 AND season_type='preseason'
        AND display_week=3 ORDER BY game_id""").fetchall()
    if week3:
        baseline = hashlib.sha256("".join(str(row["prediction_json"]) for row in week3).encode()).hexdigest()
        if len(week3) != 16 or baseline != expected_week3_hash:
            raise ValueError("Week 3 baseline verification failed during server social synchronization")
        if not all(_dt(row["generated_at"]) and _dt(row["kickoff_time"]) and
                   _dt(row["generated_at"]) < _dt(row["kickoff_time"]) for row in week3):
            raise ValueError("Week 3 frozen prediction timing verification failed")
        week3_grades = connection.execute("""SELECT prediction_result,qualified_wager
            FROM nfl_experiment_grades WHERE experiment_key='NFL-2026-PRE3'""").fetchall()
        if len(week3_grades) != 16:
            raise ValueError("Week 3 grading evidence is incomplete")
        preseason = {
            "phase": "preseason", "predictions": 16, "baseline_hash": baseline,
            "record": {result: sum(row["prediction_result"] == result for row in week3_grades)
                       for result in ("WIN", "LOSS", "PUSH")},
            "qualified_wagers": sum(int(row["qualified_wager"] or 0) for row in week3_grades),
        }
    else:
        # Some deployments intentionally do not copy historical SQLite rows into
        # PostgreSQL. Preserve the last authenticated audit evidence instead of
        # reconstructing or backfilling historical predictions.
        preseason = dict(preseason_fallback or {})
        record = preseason.get("record") or {}
        if (preseason.get("baseline_hash") != expected_week3_hash or
                int(preseason.get("predictions") or 0) != 16 or
                sum(int(record.get(result) or 0) for result in ("WIN", "LOSS", "PUSH")) != 16):
            raise ValueError("Authenticated Week 3 fallback evidence is unavailable or invalid")

    experiment = connection.execute("""SELECT e.*,s.last_status,s.last_error_code
        FROM nfl_automation_state s JOIN nfl_capture_experiments e
        ON e.experiment_key=s.active_experiment_key WHERE s.singleton=1""").fetchone()
    if not experiment:
        experiment = connection.execute("""SELECT e.*,NULL AS last_status,NULL AS last_error_code
            FROM nfl_capture_experiments e ORDER BY e.created_at DESC LIMIT 1""").fetchone()
    if not experiment:
        raise ValueError("No server NFL capture experiment is available")
    games_list = connection.execute("""SELECT * FROM nfl_capture_games WHERE experiment_key=?
        ORDER BY kickoff_epoch,game_id""", (experiment["experiment_key"],)).fetchall()
    games = {row["game_id"]: row for row in games_list}
    prediction_rows = connection.execute("""SELECT game_id,generated_at,model_version,prediction_json
        FROM nfl_game_predictions WHERE user_id=0 AND season=? AND season_type=? AND display_week=?
        ORDER BY game_id""", (experiment["season"], experiment["season_type"], experiment["display_week"])).fetchall()
    predictions: dict[str, dict[str, Any]] = {}
    prediction_bytes: list[str] = []
    for row in prediction_rows:
        payload = _json(row["prediction_json"], {})
        game = games.get(row["game_id"])
        generated = _dt(row["generated_at"])
        kickoff = _dt(game["kickoff"]) if game else None
        probability = payload.get("winProbability")
        if not game or not generated or not kickoff or generated >= kickoff or probability is None or not payload.get("winner"):
            continue
        raw = str(row["prediction_json"])
        prediction_bytes.append(raw)
        predictions[row["game_id"]] = {
            "game_id": row["game_id"], "winner": payload["winner"], "probability": float(probability),
            "generated_at": row["generated_at"], "model_version": payload.get("modelVersion") or row["model_version"],
            "model_hash": payload.get("modelHash"),
            "prediction_hash": hashlib.sha256(raw.encode()).hexdigest(),
            "rating": payload.get("rating"), "profile_eligibility": payload.get("profileEligibility") or {},
        }
    if not predictions:
        raise ValueError("No valid canonical pregame predictions are available for the active experiment")

    grade_key = f"NFL-{int(experiment['season'])}-{'PRE' if experiment['season_type']=='preseason' else 'REG'}{int(experiment['display_week'])}"
    grades = {row["game_id"]: row for row in connection.execute(
        "SELECT * FROM nfl_experiment_grades WHERE experiment_key=?", (grade_key,)).fetchall()}
    finals = {row["game_id"]: row for row in connection.execute("""SELECT * FROM nfl_game_results
        WHERE season=? AND season_type=? AND display_week=?""",
        (experiment["season"], experiment["season_type"], experiment["display_week"])).fetchall()}

    captured = _captured_markets(connection, experiment["experiment_key"], games, predictions, sha)
    markets: dict[str, dict[str, Any]] = {}
    for game_id, prediction in predictions.items():
        markets[game_id] = captured.get(game_id) or _prediction_market(
            _json(next(row["prediction_json"] for row in prediction_rows if row["game_id"] == game_id), {}),
            games[game_id], sha)
    markets = {key: value for key, value in markets.items() if value}

    operations = []
    for game in games_list:
        game_id = game["game_id"]
        final = finals.get(game_id)
        grade = grades.get(game_id)
        operations.append({
            "game_id": game_id, "away_team": game["away_team"], "home_team": game["home_team"],
            "kickoff_time": game["kickoff"], "prediction": predictions.get(game_id),
            "market": markets.get(game_id),
            "final": ({"away_score": final["away_score"], "home_score": final["home_score"],
                       "retrieved_at": final["recorded_at"] or final["source_timestamp"]} if final else None),
            "grade": ({"result": grade["prediction_result"], "graded_at": grade["graded_at"],
                       "qualified_wager": bool(grade["qualified_wager"]),
                       "grade_id": f"{grade_key}:{game_id}"} if grade else None),
        })
    record = {result: sum(row["prediction_result"] == result for row in grades.values())
              for result in ("WIN", "LOSS", "PUSH")}
    decided = record["WIN"] + record["LOSS"]
    checkpoints = connection.execute("""SELECT COUNT(*) AS total,
        SUM(CASE WHEN state IN ('CAPTURED','NO_MARKET','MISSED_CAPTURE','KICKOFF_BLOCKED') THEN 1 ELSE 0 END) AS completed
        FROM nfl_capture_checkpoints WHERE experiment_key=?""", (experiment["experiment_key"],)).fetchone()
    regular = {
        "experiment_id": grade_key, "season": experiment["season"], "phase": experiment["season_type"],
        "week": experiment["display_week"], "manifest_hash": experiment["manifest_hash"],
        "prediction_hash": hashlib.sha256("".join(prediction_bytes).encode()).hexdigest(),
        "predictions": len(predictions), "scheduled": len(games), "graded": len(grades),
        "winner_record": record, "accuracy": record["WIN"] / decided if decided else None,
        "profiles": {}, "sample_warning": "INSUFFICIENT_SAMPLE",
    }
    return {
        "verified_at": clock().astimezone(timezone.utc).isoformat(), "preseason": preseason,
        "regular": regular, "coverage_games": len(markets),
        "operations": {"schema_version": 1, "games": operations,
                       "checkpoints": {"completed": int(checkpoints["completed"] or 0),
                                       "total": int(checkpoints["total"] or 0)},
                       "provider_status": experiment["last_status"] or experiment["quota_state"]},
        "claims_policy": "predictions_are_not_wagers;small_sample_not_future_performance",
    }
