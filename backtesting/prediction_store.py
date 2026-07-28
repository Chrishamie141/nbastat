"""SQLite persistence for immutable backtest runs and predictions."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .utils import utc_now_iso
from .versioning import RunMetadata


class PredictionStore:
    """Persist run metadata, frozen predictions, grades, and report artifacts."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path)
        self._connection.row_factory = sqlite3.Row
        try:
            self.initialize()
        except BaseException:
            self.close()
            raise

    def connect(self) -> sqlite3.Connection:
        """Return the connection owned by this store."""
        if self._connection is None:
            raise RuntimeError("PredictionStore is closed")
        return self._connection

    @contextmanager
    def _cursor(self):
        """Commit a unit of work and deterministically close its cursor."""
        cursor = self.connect().cursor()
        try:
            yield cursor
            self.connect().commit()
        except BaseException:
            self.connect().rollback()
            raise
        finally:
            cursor.close()

    def close(self) -> None:
        """Commit and close the owned SQLite connection; safe to call repeatedly."""
        connection = self._connection
        if connection is None:
            return
        try:
            connection.commit()
        finally:
            try:
                connection.close()
            finally:
                self._connection = None

    def __enter__(self) -> PredictionStore:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            self.close()
        except BaseException:
            if exc_type is None:
                raise
        return False

    def initialize(self) -> None:
        """Create storage tables if they do not already exist."""
        with self._cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, model_version TEXT NOT NULL, league TEXT NOT NULL,
                    season TEXT NOT NULL, git_commit_hash TEXT, prediction_engine_version TEXT NOT NULL,
                    configuration_hash TEXT NOT NULL, date TEXT NOT NULL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, model_version TEXT NOT NULL,
                    league TEXT NOT NULL, season TEXT NOT NULL, week INTEGER NOT NULL, game TEXT,
                    prediction TEXT, confidence REAL, market TEXT, line REAL, reasoning TEXT,
                    generated_timestamp TEXT NOT NULL, actual_result TEXT, correct INTEGER, margin REAL,
                    team TEXT, player TEXT, game_type TEXT, home_away TEXT, sportsbook_odds REAL, sportsbook TEXT, edge REAL, clv REAL,
                    model_probability REAL, implied_probability REAL, prediction_model_version TEXT, features_data_as_of TEXT, features TEXT,
                    consensus_probability REAL, execution_implied_probability REAL, edge_vs_consensus REAL, edge_vs_execution REAL,
                    selection TEXT, grade TEXT, ungraded_reason TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                )
            """)
            for col, ddl in {"sportsbook_odds":"REAL", "sportsbook":"TEXT", "edge":"REAL", "clv":"REAL", "model_probability":"REAL", "implied_probability":"REAL", "consensus_probability":"REAL", "execution_implied_probability":"REAL", "edge_vs_consensus":"REAL", "edge_vs_execution":"REAL", "prediction_model_version":"TEXT", "features_data_as_of":"TEXT", "features":"TEXT", "selection":"TEXT", "grade":"TEXT", "ungraded_reason":"TEXT"}.items():
                try:
                    cursor.execute(f"ALTER TABLE predictions ADD COLUMN {col} {ddl}")
                except sqlite3.OperationalError:
                    pass
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS prediction_identity
                ON predictions(run_id, season, week, COALESCE(game,''), COALESCE(market,''),
                    COALESCE(selection,''), COALESCE(prediction_model_version,''), generated_timestamp)
            """)

    def create_run(self, metadata: RunMetadata) -> None:
        """Insert a new unique run record."""
        with self._cursor() as cursor:
            cursor.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", tuple(metadata.to_dict().values()))

    def save_prediction(self, metadata: RunMetadata, week: int, prediction: dict[str, Any]) -> int:
        """Freeze and store a single prediction before outcomes are loaded."""
        with self._cursor() as cursor:
            cursor.execute("""
                INSERT OR IGNORE INTO predictions (
                    run_id, model_version, league, season, week, game, prediction, confidence,
                    market, line, reasoning, generated_timestamp, team, player, game_type, home_away, sportsbook_odds, sportsbook, edge, clv,
                    model_probability, implied_probability, prediction_model_version, features_data_as_of, features,
                    consensus_probability, execution_implied_probability, edge_vs_consensus, edge_vs_execution, selection
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                metadata.run_id, metadata.model_version, metadata.league, metadata.season, week,
                prediction.get("game"), str(prediction.get("prediction")), prediction.get("confidence"),
                prediction.get("market"), prediction.get("line"), prediction.get("reasoning"),
                prediction.get("generated_timestamp") or utc_now_iso(), prediction.get("team"),
                prediction.get("player"), prediction.get("game_type"), prediction.get("home_away"),
                prediction.get("sportsbook_odds"), prediction.get("sportsbook"), prediction.get("edge"), prediction.get("clv"),
                prediction.get("model_probability"), prediction.get("implied_probability"), prediction.get("prediction_model_version"),
                prediction.get("features_data_as_of"), __import__("json").dumps(prediction.get("features"), sort_keys=True) if prediction.get("features") else None,
                prediction.get("consensus_probability"), prediction.get("execution_implied_probability"),
                prediction.get("edge_vs_consensus"), prediction.get("edge_vs_execution"),
                prediction.get("selection", prediction.get("prediction")),
            ))
            if cursor.rowcount:
                return int(cursor.lastrowid)
            cursor.execute("""SELECT id FROM predictions WHERE run_id=? AND season=? AND week=?
                AND game IS ? AND market IS ? AND selection IS ? AND prediction_model_version IS ?
                AND generated_timestamp=?""", (
                metadata.run_id, metadata.season, week, prediction.get("game"), prediction.get("market"),
                prediction.get("selection", prediction.get("prediction")), prediction.get("prediction_model_version"),
                prediction.get("generated_timestamp"),
            ))
            return int(cursor.fetchone()[0])

    def grade_prediction(self, prediction_id: int, grade: dict[str, Any]) -> None:
        """Attach actual result, correctness, and margin to a frozen prediction."""
        correct = grade.get("correct")
        with self._cursor() as cursor:
            cursor.execute(
                "UPDATE predictions SET actual_result=?, correct=?, margin=?, grade=?, ungraded_reason=? WHERE id=?",
                (None if grade.get("actual_result") is None else str(grade.get("actual_result")), None if correct is None else int(bool(correct)), grade.get("margin"), grade.get("grade"), grade.get("ungraded_reason"), prediction_id),
            )

    def load_predictions(self, run_id: str) -> list[dict[str, Any]]:
        """Load all stored predictions for a run."""
        with self._cursor() as cursor:
            cursor.execute("SELECT * FROM predictions WHERE run_id=? ORDER BY week, id", (run_id,))
            rows = cursor.fetchall()
        return [dict(row) for row in rows]
