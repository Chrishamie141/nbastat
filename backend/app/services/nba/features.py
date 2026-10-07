"""Leakage-safe features owned exclusively by the NBA model."""

from __future__ import annotations

import pandas as pd

TARGET_STATS = ["PTS", "REB", "AST", "STL", "BLK"]
CONTEXT_STATS = ["PTS", "REB", "AST", "STL", "BLK", "MIN"]


def prepare_features(regular_df, playoff_df):
    frame = pd.concat([regular_df, playoff_df], ignore_index=True) if playoff_df is not None and not playoff_df.empty else regular_df.copy()
    frame = frame.copy()
    frame["GAME_DATE_SORT"] = pd.to_datetime(frame["GAME_DATE"])
    frame = frame.sort_values("GAME_DATE_SORT").reset_index(drop=True)
    frame["HOME"] = frame["MATCHUP"].apply(lambda value: int("vs." in value))
    frame["PLAYOFF_GAME"] = frame["SEASON_TYPE"].apply(lambda value: int(value == "Playoffs"))
    frame["OPPONENT"] = frame["MATCHUP"].apply(lambda value: value.split()[-1])
    for stat in CONTEXT_STATS:
        frame[f"{stat}_last"] = frame[stat].shift(1)
        frame[f"{stat}_avg3"] = frame[stat].rolling(3).mean().shift(1)
        frame[f"{stat}_avg5"] = frame[stat].rolling(5).mean().shift(1)
        frame[f"{stat}_trend"] = frame[f"{stat}_avg3"] - frame[f"{stat}_avg5"]
    frame = pd.get_dummies(frame, columns=["OPPONENT"], drop_first=False).dropna().reset_index(drop=True)
    base = [f"{stat}_{key}" for stat in CONTEXT_STATS for key in ("last", "avg3", "avg5", "trend")]
    opponent = [column for column in frame.columns if column.startswith("OPPONENT_")]
    return frame, base + ["HOME", "PLAYOFF_GAME"] + opponent, opponent
