import pandas as pd

TARGET_STATS = ["PTS", "REB", "AST", "STL", "BLK"]
CONTEXT_STATS = ["PTS", "REB", "AST", "STL", "BLK", "MIN"]


def prepare_features(regular_df, playoff_df):
    df = pd.concat([regular_df, playoff_df], ignore_index=True) if playoff_df is not None and not playoff_df.empty else regular_df.copy()
    df = df.copy()
    df["GAME_DATE_SORT"] = pd.to_datetime(df["GAME_DATE"])
    df = df.sort_values("GAME_DATE_SORT").reset_index(drop=True)

    df["HOME"] = df["MATCHUP"].apply(lambda x: 1 if "vs." in x else 0)
    df["PLAYOFF_GAME"] = df["SEASON_TYPE"].apply(lambda x: 1 if x == "Playoffs" else 0)
    df["OPPONENT"] = df["MATCHUP"].apply(lambda x: x.split()[-1])

    for stat in CONTEXT_STATS:
        df[f"{stat}_last"] = df[stat].shift(1)
        df[f"{stat}_avg3"] = df[stat].rolling(3).mean().shift(1)
        df[f"{stat}_avg5"] = df[stat].rolling(5).mean().shift(1)
        df[f"{stat}_trend"] = df[f"{stat}_avg3"] - df[f"{stat}_avg5"]

    df = pd.get_dummies(df, columns=["OPPONENT"], drop_first=False)
    df = df.dropna().reset_index(drop=True)

    base_features = [f"{s}_{k}" for s in CONTEXT_STATS for k in ["last", "avg3", "avg5", "trend"]] + ["HOME", "PLAYOFF_GAME"]
    opponent_features = [col for col in df.columns if col.startswith("OPPONENT_")]
    return df, base_features + opponent_features, opponent_features
