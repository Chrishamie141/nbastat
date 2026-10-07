"""Durable NBA prediction, market, final-result, and settlement evidence."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any

from backend.app.database import column_exists, get_db_connection, using_postgres
from .grading import grade_prediction
from .season import normalize_nba_season

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dt(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def initialize() -> None:
    with get_db_connection() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS nba_games (
            game_id TEXT PRIMARY KEY, season_code TEXT NOT NULL, kickoff_time TEXT NOT NULL,
            home_team TEXT NOT NULL, away_team TEXT NOT NULL, status TEXT NOT NULL,
            home_score INTEGER, away_score INTEGER, provider TEXT NOT NULL,
            provider_event_id TEXT, provider_timestamp TEXT, retrieved_at TEXT NOT NULL,
            source_hash TEXT NOT NULL)""")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_nba_games_season_kickoff ON nba_games(season_code,kickoff_time,status)")
        connection.execute("""CREATE TABLE IF NOT EXISTS nba_prediction_snapshots (
            snapshot_id TEXT PRIMARY KEY, user_id BIGINT NOT NULL DEFAULT 0,
            game_id TEXT NOT NULL, season_code TEXT NOT NULL, kickoff_time TEXT NOT NULL,
            market TEXT NOT NULL, selection TEXT NOT NULL, line REAL, player_id TEXT,
            player_name TEXT, model_probability REAL, projection REAL, edge REAL,
            market_odds INTEGER, model_version TEXT NOT NULL, data_version TEXT,
            generated_at TEXT NOT NULL, feature_cutoff TEXT NOT NULL,
            source_provider TEXT, payload_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
            created_at TEXT NOT NULL)""")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_nba_predictions_game ON nba_prediction_snapshots(season_code,game_id,generated_at)")
        connection.execute("""CREATE TABLE IF NOT EXISTS nba_market_observations (
            observation_id TEXT PRIMARY KEY, game_id TEXT NOT NULL, season_code TEXT NOT NULL,
            provider TEXT NOT NULL, sportsbook TEXT NOT NULL, market TEXT NOT NULL,
            selection TEXT NOT NULL, line REAL, odds INTEGER NOT NULL,
            observed_at TEXT NOT NULL, kickoff_time TEXT NOT NULL,
            provider_event_id TEXT, payload_json TEXT NOT NULL, source_hash TEXT NOT NULL,
            validation_status TEXT NOT NULL DEFAULT 'VALID_PREGAME', validation_reason TEXT,
            captured_at TEXT, provenance_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
            UNIQUE(game_id,provider,sportsbook,market,selection,line,odds,observed_at))""")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_nba_markets_game_time ON nba_market_observations(game_id,observed_at)")
        additions = {
            "validation_status": "TEXT NOT NULL DEFAULT 'VALID_PREGAME'",
            "validation_reason": "TEXT", "captured_at": "TEXT",
            "provenance_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for column, definition in additions.items():
            if not column_exists(connection, "nba_market_observations", column):
                connection.execute(f"ALTER TABLE nba_market_observations ADD COLUMN {column} {definition}")
        connection.execute("""CREATE TABLE IF NOT EXISTS nba_settlements (
            prediction_snapshot_id TEXT PRIMARY KEY, game_id TEXT NOT NULL,
            status TEXT NOT NULL, actual_value TEXT, reason TEXT,
            final_source_hash TEXT NOT NULL, settled_at TEXT NOT NULL,
            FOREIGN KEY(prediction_snapshot_id) REFERENCES nba_prediction_snapshots(snapshot_id))""")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_nba_settlements_game ON nba_settlements(game_id,status)")
        connection.execute("""CREATE TABLE IF NOT EXISTS nba_replay_runs (
            run_id TEXT PRIMARY KEY, season_code TEXT NOT NULL, model_version TEXT NOT NULL,
            started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL,
            config_json TEXT NOT NULL, metrics_json TEXT, source_hash TEXT NOT NULL)""")
        if using_postgres():
            for table in ("nba_games", "nba_prediction_snapshots", "nba_market_observations", "nba_settlements", "nba_replay_runs"):
                connection.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
                connection.execute(f"REVOKE ALL ON TABLE {table} FROM anon, authenticated")
    from .registry import initialize_registry
    initialize_registry()


def upsert_game(game: dict) -> bool:
    initialize()
    season = normalize_nba_season(game["season"])
    payload = {
        "game_id": str(game["game_id"]), "season": season.code,
        "kickoff_time": str(game["kickoff_time"]), "home_team": str(game["home_team"]),
        "away_team": str(game["away_team"]), "status": str(game.get("status") or "SCHEDULED").upper(),
        "home_score": game.get("home_score"), "away_score": game.get("away_score"),
        "provider": str(game.get("provider") or "unknown"),
        "provider_event_id": game.get("provider_event_id"),
        "provider_timestamp": game.get("provider_timestamp"),
    }
    source_hash = _hash(payload)
    with get_db_connection() as connection:
        existing = connection.execute("SELECT source_hash FROM nba_games WHERE game_id=?", (payload["game_id"],)).fetchone()
        if existing and existing["source_hash"] == source_hash:
            return False
        connection.execute("""INSERT INTO nba_games
            (game_id,season_code,kickoff_time,home_team,away_team,status,home_score,away_score,
             provider,provider_event_id,provider_timestamp,retrieved_at,source_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(game_id) DO UPDATE SET season_code=excluded.season_code,
              kickoff_time=excluded.kickoff_time,home_team=excluded.home_team,away_team=excluded.away_team,
              status=excluded.status,home_score=excluded.home_score,away_score=excluded.away_score,
              provider=excluded.provider,provider_event_id=excluded.provider_event_id,
              provider_timestamp=excluded.provider_timestamp,retrieved_at=excluded.retrieved_at,
              source_hash=excluded.source_hash""",
            (payload["game_id"], season.code, payload["kickoff_time"], payload["home_team"], payload["away_team"],
             payload["status"], payload["home_score"], payload["away_score"], payload["provider"],
             payload["provider_event_id"], payload["provider_timestamp"], _now(), source_hash))
    return True


def freeze_prediction(prediction: dict, *, user_id: int = 0) -> str:
    initialize()
    generated_at = str(prediction["generated_at"])
    kickoff = str(prediction["kickoff_time"])
    feature_cutoff = str(prediction.get("feature_cutoff") or generated_at)
    if _dt(generated_at) >= _dt(kickoff) or _dt(feature_cutoff) > _dt(generated_at):
        raise ValueError("NBA predictions must be generated from features frozen strictly before kickoff")
    model_version = str(prediction.get("model_version") or "")
    if not model_version.startswith("nba-"):
        raise ValueError("NBA snapshots require an NBA-owned model version")
    season = normalize_nba_season(prediction["season"])
    payload = {**prediction, "season": season.code, "user_id": int(user_id)}
    snapshot_hash = _hash(payload)
    snapshot_id = f"nba-pred-{snapshot_hash[:32]}"
    values = (
        snapshot_id, int(user_id), str(prediction["game_id"]), season.code, kickoff,
        str(prediction["market"]).lower(), str(prediction["selection"]), prediction.get("line"),
        prediction.get("player_id"), prediction.get("player_name"), prediction.get("model_probability"),
        prediction.get("projection"), prediction.get("edge"), prediction.get("market_odds"),
        model_version, prediction.get("data_version"), generated_at, feature_cutoff,
        prediction.get("source_provider"), _json(payload), snapshot_hash, _now(),
    )
    with get_db_connection() as connection:
        connection.execute("""INSERT INTO nba_prediction_snapshots
            (snapshot_id,user_id,game_id,season_code,kickoff_time,market,selection,line,player_id,
             player_name,model_probability,projection,edge,market_odds,model_version,data_version,
             generated_at,feature_cutoff,source_provider,payload_json,snapshot_hash,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(snapshot_id) DO NOTHING""", values)
    logger.info("nba_prediction_frozen %s", _json({"snapshotId": snapshot_id, "gameId": prediction["game_id"], "modelVersion": model_version}))
    return snapshot_id


def append_market_observation(observation: dict) -> str:
    initialize()
    observed_at, kickoff = str(observation["observed_at"]), str(observation["kickoff_time"])
    if _dt(observed_at) >= _dt(kickoff):
        raise ValueError("NBA market observations must be strictly before kickoff")
    if not str(observation.get("provider") or "").strip() or not str(observation.get("sportsbook") or "").strip():
        raise ValueError("NBA market observations require provider and sportsbook provenance")
    try:
        odds = int(observation["odds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("NBA market observations require valid American odds") from exc
    if odds == 0:
        raise ValueError("NBA market observations require non-zero American odds")
    season = normalize_nba_season(observation["season"])
    payload = {**observation, "season": season.code}
    source_hash = _hash(payload)
    observation_id = f"nba-market-{source_hash[:32]}"
    with get_db_connection() as connection:
        connection.execute("""INSERT INTO nba_market_observations
            (observation_id,game_id,season_code,provider,sportsbook,market,selection,line,odds,
             observed_at,kickoff_time,provider_event_id,payload_json,source_hash,validation_status,
             validation_reason,captured_at,provenance_json,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(observation_id) DO NOTHING""",
            (observation_id, str(observation["game_id"]), season.code, str(observation["provider"]),
             str(observation["sportsbook"]), str(observation["market"]).lower(), str(observation["selection"]),
             observation.get("line"), odds, observed_at, kickoff,
             observation.get("provider_event_id"), _json(payload), source_hash, "VALID_PREGAME",
             None, _now(), _json(observation.get("provenance") or {
                 "provider": observation["provider"], "sportsbook": observation["sportsbook"],
             }), _now()))
    return observation_id


def settle_game(game_id: str, *, player_stats: dict | None = None) -> dict:
    initialize()
    settled = pending = already = 0
    with get_db_connection() as connection:
        game_row = connection.execute("SELECT * FROM nba_games WHERE game_id=?", (game_id,)).fetchone()
        if not game_row:
            return {"settled": 0, "pending": 0, "alreadySettled": 0, "reason": "game_not_found"}
        game = dict(game_row)
        game["player_stats"] = player_stats or {}
        predictions = connection.execute("""SELECT p.* FROM nba_prediction_snapshots p
            WHERE p.game_id=? ORDER BY p.generated_at,p.snapshot_id""", (game_id,)).fetchall()
        for raw in predictions:
            prediction = dict(raw)
            existing = connection.execute("SELECT status FROM nba_settlements WHERE prediction_snapshot_id=?", (prediction["snapshot_id"],)).fetchone()
            if existing and existing["status"] != "UNRESOLVED":
                already += 1
                continue
            result = grade_prediction(prediction, game)
            if result["status"] == "UNRESOLVED":
                pending += 1
            connection.execute("""INSERT INTO nba_settlements
                (prediction_snapshot_id,game_id,status,actual_value,reason,final_source_hash,settled_at)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(prediction_snapshot_id) DO NOTHING""",
                (prediction["snapshot_id"], game_id, result["status"],
                 None if result["actual"] is None else str(result["actual"]), result["reason"],
                 game["source_hash"], _now()))
            if existing and existing["status"] == "UNRESOLVED" and result["status"] != "UNRESOLVED":
                connection.execute("""UPDATE nba_settlements SET status=?,actual_value=?,reason=?,
                    final_source_hash=?,settled_at=? WHERE prediction_snapshot_id=? AND status='UNRESOLVED'""",
                    (result["status"], None if result["actual"] is None else str(result["actual"]),
                     result["reason"], game["source_hash"], _now(), prediction["snapshot_id"]))
            if result["status"] != "UNRESOLVED":
                settled += 1
    logger.info("nba_game_settlement %s", _json({"gameId": game_id, "settled": settled, "pending": pending, "already": already}))
    return {"settled": settled, "pending": pending, "alreadySettled": already}


def persist_replay_summary(summary: dict, *, config: dict) -> str:
    """Persist one immutable replay result and update research evidence."""
    initialize()
    payload = {"summary": summary, "config": config}
    source_hash = _hash(payload)
    run_id = f"nba-replay-{source_hash[:32]}"
    with get_db_connection() as connection:
        connection.execute("""INSERT INTO nba_replay_runs
            (run_id,season_code,model_version,started_at,completed_at,status,
             config_json,metrics_json,source_hash) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id) DO NOTHING""",
            (run_id, normalize_nba_season(summary["season"]).code, summary["modelVersion"],
             _now(), _now(), "COMPLETE", _json(config), _json(summary["metrics"]), source_hash))
    from .registry import record_evaluation
    periods = sorted((summary.get("metrics", {}).get("byTimePeriod") or {}).keys())
    record_evaluation(model_version=summary["modelVersion"], metrics=summary["metrics"],
                      evaluation_start=periods[0] if periods else None,
                      evaluation_end=periods[-1] if periods else None)
    return run_id


def foundation_status(season: str | int) -> dict:
    initialize()
    code = normalize_nba_season(season).code
    with get_db_connection() as connection:
        counts = {}
        for key, table in (("games", "nba_games"), ("predictions", "nba_prediction_snapshots"),
                           ("markets", "nba_market_observations"), ("settlements", "nba_settlements"),
                           ("replayRuns", "nba_replay_runs")):
            column = "season_code" if table != "nba_settlements" else None
            if column:
                row = connection.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE {column}=?", (code,)).fetchone()
            else:
                row = connection.execute("""SELECT COUNT(*) AS count FROM nba_settlements s
                    JOIN nba_prediction_snapshots p ON p.snapshot_id=s.prediction_snapshot_id
                    WHERE p.season_code=?""", (code,)).fetchone()
            counts[key] = int(row["count"])
    from .registry import registry_status
    return {"league": "NBA", "season": code, "modelIsolation": "NBA_ONLY",
            "productionReady": False, "modelRegistry": registry_status(), **counts}
