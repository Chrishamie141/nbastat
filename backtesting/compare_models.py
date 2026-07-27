"""Offline, paired comparison of model versions on immutable snapshots."""
from __future__ import annotations

import argparse, csv, json, tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .config import BacktestConfig, SNAPSHOTS_DIR
from .evaluation import betting_metrics, confidence_buckets, edge_buckets, probability_metrics
from .markets import normalize_markets
from .replay_engine import ReplayEngine

WARNING = "Exploratory historical sample: results are insufficient to establish model superiority or generalization."


def market_metrics(rows: list[dict[str, Any]], candidates: int | None = None) -> dict[str, Any]:
    metric = betting_metrics([{**r, "bet": True, "odds_used": r["sportsbook_odds"]} for r in rows])
    binary = [(r["model_probability"], 1 if r["grade"] == "win" else 0) for r in rows
              if r.get("grade") in {"win", "loss"} and r.get("model_probability") is not None]
    def avg(key):
        values = [float(r[key]) for r in rows if r.get(key) is not None]
        return sum(values)/len(values) if values else None
    return {"candidates": len(rows) if candidates is None else candidates, "accepted_bets": len(rows), **metric,
            "average_odds": avg("sportsbook_odds"), "average_model_probability": avg("model_probability"),
            "average_implied_probability": avg("execution_implied_probability") or avg("implied_probability"),
            "average_consensus_probability": avg("consensus_probability"), "average_edge": avg("edge"),
            "probability_quality": probability_metrics(binary), "edge_buckets": edge_buckets(
                [{**r, "bet": True, "odds_used": r["sportsbook_odds"]} for r in rows]),
            "confidence_buckets": confidence_buckets(
                [{**r, "bet": True, "odds_used": r["sportsbook_odds"]} for r in rows])}


def paired_decisions(model_rows: dict[str, list[dict[str, Any]]], models: list[str], universe: set[tuple[str, str]]) -> dict[str, Any]:
    left, right = ({(r["game"], r["market"]): r for r in model_rows[m]} for m in models[:2])
    counts = Counter(); details = []
    for key in sorted(universe):
        a, b = left.get(key), right.get(key)
        if a and b: category = "both_same_selection" if a["selection"].casefold() == b["selection"].casefold() else "both_opposite_selections"
        elif a: category = "v1_only"
        elif b: category = "v2_only"
        else: category = "neither"
        counts[category] += 1
        details.append({"game_id": key[0], "market": key[1], "category": category,
            "selection_difference": bool(a and b and a["selection"].casefold() != b["selection"].casefold()),
            "probability_difference_v2_minus_v1": (b["model_probability"]-a["model_probability"]) if a and b else None,
            "edge_difference_v2_minus_v1": (b["edge"]-a["edge"]) if a and b else None,
            "result_v1": a.get("grade") if a else None, "result_v2": b.get("grade") if b else None})
    return {"counts": dict(sorted(counts.items())), "decisions": details}


def compare(*, data_dir: Path, league: str, season: str, start_week: int, end_week: int,
            markets: tuple[str, ...], models: tuple[str, ...]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    markets = normalize_markets(markets)
    rows_by_model = {}; summaries = {}; universe = set()
    with tempfile.TemporaryDirectory() as temp:
        for model in models:
            cfg = BacktestConfig(league=league, season=season, start_week=start_week, end_week=end_week,
                markets=markets, model_version=model, data_dir=data_dir, db_path=Path(temp)/f"{model}.db", export=False)
            with ReplayEngine(cfg) as engine:
                summary = engine.run()
                rows = engine.store.load_predictions(summary["run_id"])
            game_meta = {g["game_id"]: g for g in summary["evaluation"].get("games", [])}
            for row in rows:
                row["run_id"] = summary["run_id"]
                meta = game_meta.get(row["game"], {})
                row.update({"model_version": model, "game_id": row["game"], "kickoff": meta.get("kickoff"),
                    "home_team": meta.get("home_team"), "away_team": meta.get("away_team"),
                    "odds": row.get("sportsbook_odds")})
                try: row["features"] = json.loads(row["features"] or "{}")
                except (TypeError, json.JSONDecodeError): row["features"] = {}
            rows_by_model[model] = rows
            universe.update((g["game_id"], d["market"]) for g in summary["evaluation"].get("games", []) for d in g.get("market_decisions", []))
            by_market = {market: [r for r in rows if r["market"] == market] for market in markets}
            summaries[model] = {"overall": market_metrics(rows), "by_market": {m: market_metrics(v) for m,v in by_market.items()},
                "replay_evaluation": summary["evaluation"]}
    report = {"schema_version": 1, "league": league, "season": season, "start_week": start_week,
        "end_week": end_week, "models": summaries, "paired": paired_decisions(rows_by_model, list(models), universe),
        "warning": WARNING}
    return report, [r for model in models for r in rows_by_model[model]]


def write_artifacts(report, rows, output: Path, bets: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, default=str)+"\n", encoding="utf-8")
    bets.parent.mkdir(parents=True, exist_ok=True)
    audit = []
    for r in rows:
        features = r.pop("features", {}) or {}
        audit.append({**r, "result": r.get("grade"), "profit_units": (r["sportsbook_odds"]/100 if r["sportsbook_odds"] > 0 else 100/abs(r["sportsbook_odds"])) if r["grade"] == "win" else -1 if r["grade"] == "loss" else 0,
            "rest_difference": ((features.get("home_days_since_last_game") or 0)-(features.get("away_days_since_last_game") or 0)),
            **{k: features.get(k) for k in ("projected_home_points", "projected_away_points", "projected_margin", "projected_total", "home_elo", "away_elo")}})
    fields = sorted({k for r in audit for k in r})
    with bets.open("w", newline="", encoding="utf-8") as handle:
        writer=csv.DictWriter(handle, fields); writer.writeheader(); writer.writerows(audit)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--league", default="nfl"); p.add_argument("--season", required=True)
    p.add_argument("--start-week", type=int, required=True); p.add_argument("--end-week", type=int, required=True)
    p.add_argument("--markets", default="h2h,spread,total"); p.add_argument("--models", default="nfl_game_baseline_v1,nfl_game_baseline_v2")
    p.add_argument("--data-dir", type=Path, default=SNAPSHOTS_DIR); p.add_argument("--output", type=Path, default=Path("reports/model_comparison.json")); p.add_argument("--bets", type=Path, default=Path("reports/model_comparison_bets.csv"))
    a=p.parse_args(); report, rows=compare(data_dir=a.data_dir, league=a.league, season=a.season, start_week=a.start_week, end_week=a.end_week, markets=tuple(a.markets.split(",")), models=tuple(a.models.split(",")))
    write_artifacts(report, rows, a.output, a.bets); print(WARNING)

if __name__ == "__main__": main()
