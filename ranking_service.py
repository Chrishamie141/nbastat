STAT_LABELS = {"PTS": "POINTS", "REB": "REBOUNDS", "AST": "ASSISTS", "STL": "STEALS", "BLK": "BLOCKS"}


def confidence_from_mae(prediction, mae):
    normalized_error = mae / max(prediction, 1)
    score = max(0, round(100 - normalized_error * 100, 1))
    if score >= 80:
        label = "HIGH"
    elif score >= 60:
        label = "MEDIUM"
    else:
        label = "LOW"
    return score, label


def build_rankings(player_results):
    by_stat = {s: [] for s in STAT_LABELS}
    for item in player_results:
        for stat in STAT_LABELS:
            pred = item["result"]["blended_prediction"][stat]
            mae = item["result"]["model_error"][stat]
            score, label = confidence_from_mae(pred, mae)
            by_stat[stat].append({
                "player": item["player"], "prediction": pred, "range": item["result"]["range"][stat],
                "score": score, "label": label
            })
    return by_stat
