from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import boxscoretraditionalv2

from ranking_service import confidence_from_mae

HISTORY_FILE = Path("data/prediction_history.csv")
HISTORY_COLUMNS = [
    "prediction_id",
    "created_at",
    "game_id",
    "game_date",
    "team",
    "opponent",
    "home_away",
    "player",
    "stat_type",
    "predicted_value",
    "range_low",
    "range_high",
    "reliability_score",
    "confidence_label",
    "actual_value",
    "absolute_error",
    "squared_error",
    "percent_error",
    "hit_range",
    "graded",
    "graded_at",
]

STAT_COLUMNS = ["PTS", "REB", "AST", "STL", "BLK"]


def _utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean_value(value):
    if value is None:
        return ""
    return str(value).strip()


def _make_prediction_id(game_id, game_date, team, opponent, home_away, player, stat_type):
    raw = "|".join(
        _clean_value(value).upper()
        for value in [game_id, game_date, team, opponent, home_away, player, stat_type]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _empty_history():
    return pd.DataFrame(columns=HISTORY_COLUMNS)


def load_prediction_history(history_file=HISTORY_FILE):
    history_path = Path(history_file)
    if not history_path.exists():
        return _empty_history()

    history = pd.read_csv(history_path, dtype={"game_id": str, "prediction_id": str})
    for column in HISTORY_COLUMNS:
        if column not in history.columns:
            history[column] = ""
    return history[HISTORY_COLUMNS]


def update_prediction_history(history, history_file=HISTORY_FILE):
    history_path = Path(history_file)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history = history.copy()
    for column in HISTORY_COLUMNS:
        if column not in history.columns:
            history[column] = ""
    history[HISTORY_COLUMNS].to_csv(history_path, index=False)


def save_prediction(
    *,
    game_id,
    game_date,
    team,
    opponent,
    home_away,
    player,
    stat_type,
    predicted_value,
    range_low,
    range_high,
    reliability_score,
    confidence_label,
    created_at=None,
    history_file=HISTORY_FILE,
):
    history = load_prediction_history(history_file)
    prediction_id = _make_prediction_id(
        game_id, game_date, team, opponent, home_away, player, stat_type
    )

    if not history.empty and prediction_id in set(history["prediction_id"].astype(str)):
        return False

    row = {
        "prediction_id": prediction_id,
        "created_at": created_at or _utc_now_iso(),
        "game_id": _clean_value(game_id),
        "game_date": _clean_value(game_date),
        "team": _clean_value(team),
        "opponent": _clean_value(opponent),
        "home_away": _clean_value(home_away),
        "player": _clean_value(player),
        "stat_type": _clean_value(stat_type).upper(),
        "predicted_value": float(predicted_value),
        "range_low": float(range_low),
        "range_high": float(range_high),
        "reliability_score": float(reliability_score),
        "confidence_label": _clean_value(confidence_label),
        "actual_value": "",
        "absolute_error": "",
        "squared_error": "",
        "percent_error": "",
        "hit_range": "",
        "graded": False,
        "graded_at": "",
    }

    history = pd.concat([history, pd.DataFrame([row])], ignore_index=True)
    update_prediction_history(history, history_file)
    return True


def save_player_predictions(result, team, context, history_file=HISTORY_FILE):
    created_at = _utc_now_iso()
    saved_count = 0
    home_away = "Home" if context.get("home") else "Away/Unknown"

    for stat in STAT_COLUMNS:
        predicted_value = result["blended_prediction"][stat]
        range_low, range_high = result["range"][stat]
        reliability_score, confidence_label = confidence_from_mae(
            predicted_value, result["model_error"][stat], stat
        )
        saved = save_prediction(
            game_id=context.get("game_id"),
            game_date=context.get("game_date"),
            team=team,
            opponent=context.get("opponent"),
            home_away=home_away,
            player=result["player"],
            stat_type=stat,
            predicted_value=predicted_value,
            range_low=range_low,
            range_high=range_high,
            reliability_score=reliability_score,
            confidence_label=confidence_label,
            created_at=created_at,
            history_file=history_file,
        )
        if saved:
            saved_count += 1

    return saved_count


def _normalize_name(name):
    return " ".join(str(name).lower().replace(".", "").split())


def _fetch_actual_box_score(game_id, timeout=30):
    box_score = boxscoretraditionalv2.BoxScoreTraditionalV2(
        game_id=str(game_id),
        timeout=timeout,
    )
    player_stats = box_score.player_stats.get_data_frame()
    if player_stats.empty:
        raise ValueError(f"No box score player stats found for game_id {game_id}.")
    return player_stats


def grade_predictions_for_game(game_id, history_file=HISTORY_FILE, timeout=30):
    history = load_prediction_history(history_file)
    if history.empty:
        print("No prediction history found.")
        return _empty_history()

    game_id = str(game_id).strip()
    mask = (history["game_id"].astype(str) == game_id) & (
        ~history["graded"].astype(str).str.lower().eq("true")
    )
    if not mask.any():
        print(f"No ungraded predictions found for game_id {game_id}.")
        return _empty_history()

    actuals = _fetch_actual_box_score(game_id, timeout=timeout)
    actuals["PLAYER_KEY"] = actuals["PLAYER_NAME"].apply(_normalize_name)
    actual_lookup = actuals.set_index("PLAYER_KEY")

    graded_rows = []
    graded_at = _utc_now_iso()

    for idx, row in history[mask].iterrows():
        player_key = _normalize_name(row["player"])
        stat_type = str(row["stat_type"]).upper()

        if player_key not in actual_lookup.index or stat_type not in actual_lookup.columns:
            continue

        actual_row = actual_lookup.loc[player_key]
        if isinstance(actual_row, pd.DataFrame):
            actual_row = actual_row.iloc[0]

        actual_value = float(actual_row[stat_type])
        predicted_value = float(row["predicted_value"])
        range_low = float(row["range_low"])
        range_high = float(row["range_high"])
        absolute_error = abs(predicted_value - actual_value)

        history.at[idx, "actual_value"] = actual_value
        history.at[idx, "absolute_error"] = absolute_error
        history.at[idx, "squared_error"] = absolute_error * absolute_error
        history.at[idx, "percent_error"] = absolute_error / actual_value if actual_value > 0 else ""
        history.at[idx, "hit_range"] = range_low <= actual_value <= range_high
        history.at[idx, "graded"] = True
        history.at[idx, "graded_at"] = graded_at
        graded_rows.append(history.loc[idx].copy())

    update_prediction_history(history, history_file)
    graded = pd.DataFrame(graded_rows, columns=HISTORY_COLUMNS)
    print(f"Graded {len(graded)} predictions for game_id {game_id}.")
    if not graded.empty:
        show_accuracy_report(graded)
    return graded


def _graded_history(history):
    if history.empty:
        return history
    graded = history[history["graded"].astype(str).str.lower().eq("true")].copy()
    for column in ["absolute_error", "squared_error", "hit_range"]:
        if column in graded.columns:
            if column == "hit_range":
                graded[column] = graded[column].astype(str).str.lower().eq("true")
            else:
                graded[column] = pd.to_numeric(graded[column], errors="coerce")
    return graded.dropna(subset=["absolute_error", "squared_error"])


def show_accuracy_report(history=None, history_file=HISTORY_FILE):
    if history is None:
        history = load_prediction_history(history_file)

    graded = _graded_history(history)
    if graded.empty:
        print("No graded predictions available yet.")
        return pd.DataFrame()

    rows = []
    print("\nMODEL ACCURACY REPORT")
    for stat in STAT_COLUMNS:
        stat_df = graded[graded["stat_type"].astype(str).str.upper() == stat]
        if stat_df.empty:
            continue

        mae = float(stat_df["absolute_error"].mean())
        rmse = float(stat_df["squared_error"].mean() ** 0.5)
        hit_rate = float(stat_df["hit_range"].mean())
        rows.append(
            {
                "stat_type": stat,
                "mae": mae,
                "rmse": rmse,
                "range_hit_rate": hit_rate,
                "graded_predictions": int(len(stat_df)),
            }
        )

        print(f"\n{stat}:")
        print(f"MAE: {mae:.1f}")
        print(f"RMSE: {rmse:.1f}")
        print(f"Range Hit Rate: {hit_rate:.0%}")

    summary = pd.DataFrame(rows)
    if summary.empty:
        print("No graded predictions available yet.")
        return summary

    total = int(len(graded))
    best = summary.sort_values(["mae", "rmse"], ascending=[True, True]).iloc[0]
    worst = summary.sort_values(["mae", "rmse"], ascending=[False, False]).iloc[0]

    print(f"\nTotal graded predictions: {total}")
    print(f"Best stat category: {best['stat_type']} (MAE {best['mae']:.1f})")
    print(f"Worst stat category: {worst['stat_type']} (MAE {worst['mae']:.1f})")
    return summary
