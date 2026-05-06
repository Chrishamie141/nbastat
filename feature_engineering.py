import pandas as pd


def prepare_features(regular_df, playoff_df):
    if playoff_df is not None and not playoff_df.empty:
        df = pd.concat([regular_df, playoff_df], ignore_index=True)
    else:
        df = regular_df.copy()

    df = df.copy()

    df["GAME_DATE_SORT"] = pd.to_datetime(df["GAME_DATE"])
    df = df.sort_values("GAME_DATE_SORT").reset_index(drop=True)

    df["HOME"] = df["MATCHUP"].apply(lambda x: 1 if "vs." in x else 0)
    df["PLAYOFF_GAME"] = df["SEASON_TYPE"].apply(lambda x: 1 if x == "Playoffs" else 0)
    df["OPPONENT"] = df["MATCHUP"].apply(lambda x: x.split()[-1])

    stat_cols = ["PTS", "REB", "AST", "MIN"]

    for stat in stat_cols:
        df[f"{stat}_last"] = df[stat].shift(1)
        df[f"{stat}_avg3"] = df[stat].rolling(3).mean().shift(1)
        df[f"{stat}_avg5"] = df[stat].rolling(5).mean().shift(1)
        df[f"{stat}_trend"] = df[f"{stat}_avg3"] - df[f"{stat}_avg5"]

    df = pd.get_dummies(df, columns=["OPPONENT"], drop_first=False)
    df = df.dropna().reset_index(drop=True)

    base_features = [
        "PTS_last", "REB_last", "AST_last", "MIN_last",
        "PTS_avg3", "REB_avg3", "AST_avg3", "MIN_avg3",
        "PTS_avg5", "REB_avg5", "AST_avg5", "MIN_avg5",
        "PTS_trend", "REB_trend", "AST_trend", "MIN_trend",
        "HOME", "PLAYOFF_GAME"
    ]

    opponent_features = [col for col in df.columns if col.startswith("OPPONENT_")]
    features = base_features + opponent_features

    return df, features, opponent_features