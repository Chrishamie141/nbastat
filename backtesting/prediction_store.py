"""SQLite persistence for immutable backtest runs and predictions."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .utils import utc_now_iso
from .versioning import RunMetadata


class PredictionStore:
    """Persist run metadata, frozen predictions, grades, and report artifacts."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        """Open a row-aware SQLite connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        """Create storage tables if they do not already exist."""
        with self.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, model_version TEXT NOT NULL, league TEXT NOT NULL,
                    season TEXT NOT NULL, git_commit_hash TEXT, prediction_engine_version TEXT NOT NULL,
                    configuration_hash TEXT NOT NULL, date TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, model_version TEXT NOT NULL,
                    league TEXT NOT NULL, season TEXT NOT NULL, week INTEGER NOT NULL, game TEXT,
                    prediction TEXT, confidence REAL, market TEXT, line REAL, reasoning TEXT,
                    generated_timestamp TEXT NOT NULL, actual_result TEXT, correct INTEGER, margin REAL,
                    team TEXT, player TEXT, game_type TEXT, home_away TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                )
            """)

    def create_run(self, metadata: RunMetadata) -> None:
        """Insert a new unique run record."""
        with self.connect() as conn:
            conn.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", tuple(metadata.to_dict().values()))

    def save_prediction(self, metadata: RunMetadata, week: int, prediction: dict[str, Any]) -> int:
        """Freeze and store a single prediction before outcomes are loaded."""
        with self.connect() as conn:
            cur = conn.execute("""
                INSERT INTO predictions (
                    run_id, model_version, league, season, week, game, prediction, confidence,
                    market, line, reasoning, generated_timestamp, team, player, game_type, home_away
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                metadata.run_id, metadata.model_version, metadata.league, metadata.season, week,
                prediction.get("game"), str(prediction.get("prediction")), prediction.get("confidence"),
                prediction.get("market"), prediction.get("line"), prediction.get("reasoning"),
                prediction.get("generated_timestamp") or utc_now_iso(), prediction.get("team"),
                prediction.get("player"), prediction.get("game_type"), prediction.get("home_away"),
            ))
            return int(cur.lastrowid)

    def grade_prediction(self, prediction_id: int, grade: dict[str, Any]) -> None:
        """Attach actual result, correctness, and margin to a frozen prediction."""
        correct = grade.get("correct")
        with self.connect() as conn:
            conn.execute(
                "UPDATE predictions SET actual_result=?, correct=?, margin=? WHERE id=?",
                (None if grade.get("actual_result") is None else str(grade.get("actual_result")), None if correct is None else int(bool(correct)), grade.get("margin"), prediction_id),
            )

    def load_predictions(self, run_id: str) -> list[dict[str, Any]]:
        """Load all stored predictions for a run."""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM predictions WHERE run_id=? ORDER BY week, id", (run_id,)).fetchall()
        return [dict(row) for row in rows]
