"""SQLite storage for predictions, bet recommendations, and graded bets."""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_FILE = Path("predictions.db")


def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_connection(db_file=DB_FILE):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database(db_file=DB_FILE):
    with get_connection(db_file) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_date TEXT,
                team TEXT,
                opponent TEXT,
                player TEXT NOT NULL,
                stat_type TEXT NOT NULL,
                projection REAL NOT NULL,
                low_range REAL NOT NULL,
                high_range REAL NOT NULL,
                confidence_score REAL NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bet_recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id INTEGER,
                player TEXT NOT NULL,
                stat_type TEXT NOT NULL,
                line REAL NOT NULL,
                sportsbook_odds INTEGER NOT NULL,
                sportsbook_probability REAL NOT NULL,
                model_probability REAL NOT NULL,
                edge REAL NOT NULL,
                strength TEXT NOT NULL,
                recommended INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(prediction_id) REFERENCES predictions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS graded_bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player TEXT NOT NULL,
                stat_type TEXT NOT NULL,
                line REAL NOT NULL,
                projection REAL,
                actual_result REAL NOT NULL,
                hit INTEGER NOT NULL,
                sportsbook_odds INTEGER NOT NULL,
                stake REAL NOT NULL,
                profit_loss REAL NOT NULL,
                game_date TEXT,
                created_at TEXT NOT NULL
            )
            """
        )


def save_prediction_record(prediction, db_file=DB_FILE):
    initialize_database(db_file)
    with get_connection(db_file) as conn:
        cur = conn.execute(
            """
            INSERT INTO predictions (
                game_date, team, opponent, player, stat_type, projection,
                low_range, high_range, confidence_score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prediction.get("game_date"),
                prediction.get("team"),
                prediction.get("opponent"),
                prediction["player"],
                prediction["stat_type"],
                float(prediction["projection"]),
                float(prediction["low_range"]),
                float(prediction["high_range"]),
                float(prediction.get("confidence_score", 0)),
                prediction.get("created_at") or utc_now_iso(),
            ),
        )
        return cur.lastrowid


def save_bet_recommendations(recommendations, db_file=DB_FILE):
    initialize_database(db_file)
    created_at = utc_now_iso()
    saved_ids = []
    with get_connection(db_file) as conn:
        for bet in recommendations:
            cur = conn.execute(
                """
                INSERT INTO bet_recommendations (
                    prediction_id, player, stat_type, line, sportsbook_odds,
                    sportsbook_probability, model_probability, edge, strength,
                    recommended, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bet.get("prediction_id"),
                    bet["player"],
                    bet["stat_type"],
                    float(bet["line"]),
                    int(bet["sportsbook_odds"]),
                    float(bet["sportsbook_probability"]),
                    float(bet["model_probability"]),
                    float(bet["edge"]),
                    bet["strength"],
                    1 if bet.get("recommended") else 0,
                    created_at,
                ),
            )
            saved_ids.append(cur.lastrowid)
    return saved_ids


def load_recommendations_for_grading(db_file=DB_FILE):
    initialize_database(db_file)
    with get_connection(db_file) as conn:
        rows = conn.execute(
            """
            SELECT br.*, p.projection, p.game_date
            FROM bet_recommendations br
            LEFT JOIN predictions p ON p.id = br.prediction_id
            WHERE br.recommended = 1
            ORDER BY br.created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _normalize_name(name):
    return " ".join(str(name).lower().replace(".", "").split())


def american_profit(odds, stake, hit):
    stake = float(stake)
    odds = int(odds)
    if not hit:
        return -stake
    if odds < 0:
        return stake * (100 / abs(odds))
    return stake * (odds / 100)


def grade_recommendations(actual_results, default_stake=10.0, db_file=DB_FILE):
    recommendations = load_recommendations_for_grading(db_file)
    lookup = {
        (_normalize_name(row["player"]), str(row["stat_type"]).upper(), str(row.get("game_date") or "")): float(row["actual_result"])
        for row in actual_results
    }
    fallback_lookup = {
        (_normalize_name(row["player"]), str(row["stat_type"]).upper()): float(row["actual_result"])
        for row in actual_results
    }

    graded = []
    created_at = utc_now_iso()
    with get_connection(db_file) as conn:
        for rec in recommendations:
            key = (_normalize_name(rec["player"]), rec["stat_type"], str(rec.get("game_date") or ""))
            actual = lookup.get(key)
            if actual is None:
                actual = fallback_lookup.get((_normalize_name(rec["player"]), rec["stat_type"]))
            if actual is None:
                continue
            hit = actual >= float(rec["line"])
            stake = float(rec.get("stake") or default_stake)
            profit_loss = american_profit(rec["sportsbook_odds"], stake, hit)
            row = {
                "player": rec["player"],
                "stat_type": rec["stat_type"],
                "line": float(rec["line"]),
                "projection": rec.get("projection"),
                "actual_result": actual,
                "hit": hit,
                "sportsbook_odds": int(rec["sportsbook_odds"]),
                "stake": stake,
                "profit_loss": profit_loss,
                "game_date": rec.get("game_date"),
                "created_at": created_at,
            }
            conn.execute(
                """
                INSERT INTO graded_bets (
                    player, stat_type, line, projection, actual_result, hit,
                    sportsbook_odds, stake, profit_loss, game_date, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["player"], row["stat_type"], row["line"], row["projection"],
                    row["actual_result"], 1 if row["hit"] else 0, row["sportsbook_odds"],
                    row["stake"], row["profit_loss"], row["game_date"], row["created_at"]
                ),
            )
            graded.append(row)
    return graded


def load_actual_results_from_csv(csv_path):
    with open(csv_path, newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        required = {"player", "stat_type", "actual_result", "game_date"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV missing required columns: {', '.join(sorted(missing))}")
        return [row for row in reader]


def summarize_graded_bets(graded_rows):
    total = len(graded_rows)
    if total == 0:
        return {"total": 0, "hit_rate": 0, "roi": 0, "by_stat": {}, "profits_by_stat": {}}
    total_staked = sum(float(row["stake"]) for row in graded_rows)
    total_profit = sum(float(row["profit_loss"]) for row in graded_rows)
    by_stat = {}
    profits_by_stat = {}
    for row in graded_rows:
        stat = row["stat_type"]
        by_stat.setdefault(stat, []).append(1 if row["hit"] else 0)
        profits_by_stat[stat] = profits_by_stat.get(stat, 0.0) + float(row["profit_loss"])
    return {
        "total": total,
        "hit_rate": sum(1 if row["hit"] else 0 for row in graded_rows) / total,
        "roi": total_profit / total_staked if total_staked else 0,
        "by_stat": {stat: sum(values) / len(values) for stat, values in by_stat.items()},
        "profits_by_stat": profits_by_stat,
    }
