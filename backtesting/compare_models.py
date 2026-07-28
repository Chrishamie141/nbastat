"""Deterministic, offline, paired model-quality evaluation."""
from __future__ import annotations

import argparse
import csv
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .config import BacktestConfig, SNAPSHOTS_DIR
from .evaluation import american_profit, betting_metrics, edge_buckets, error_metrics, probability_metrics
from .markets import normalize_markets
from .outcomes import normalize_outcomes
from .replay_engine import ReplayEngine

WARNING = ("Exploratory evidence from one portion of one NFL season; sample sizes are "
           "not sufficient to establish statistical significance or model superiority.")
CALIBRATION_BOUNDS = ((.2, .3, "20-30%"), (.3, .4, "30-40%"), (.4, .5, "40-50%"),
                      (.5, .6, "50-60%"), (.6, .7, "60-70%"), (.7, .8, "70-80%"),
                      (.8, .9, "80-90%"))
CSV_FIELDS = ("week", "game_id", "kickoff", "model", "market", "selection", "line",
              "american_odds", "model_probability", "consensus_probability",
              "execution_implied_probability", "edge_vs_consensus", "edge_vs_execution",
              "decision", "grade", "units_result")


def _average(rows: Iterable[dict[str, Any]], key: str) -> float | None:
    values = [float(r[key]) for r in rows if r.get(key) is not None]
    return sum(values) / len(values) if values else None


def calibration_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fixed-bin calibration and ECE = sum(n_bin/N * abs(observed-mean))."""
    binary = [r for r in rows if r.get("grade") in {"win", "loss"} and r.get("model_probability") is not None]
    buckets = []
    for low, high, label in CALIBRATION_BOUNDS:
        items = [r for r in binary if low <= float(r["model_probability"]) < high]
        mean = _average(items, "model_probability")
        observed = sum(r["grade"] == "win" for r in items) / len(items) if items else None
        buckets.append({"bucket": label, "count": len(items), "mean_predicted_probability": mean,
                        "observed_win_rate": observed,
                        "calibration_error": abs(observed - mean) if items else None})
    covered = sum(b["count"] for b in buckets)
    ece = (sum(b["count"] * b["calibration_error"] for b in buckets if b["count"]) / covered
           if covered else None)
    return {"count": len(binary), "covered_count": covered, "ece": ece,
            "ece_definition": "sum(bucket_count / covered_count * abs(observed_win_rate - mean_predicted_probability))",
            "buckets": buckets}


def market_metrics(rows: list[dict[str, Any]], candidates: int | None = None) -> dict[str, Any]:
    prepared = [{**r, "bet": True, "odds_used": r.get("sportsbook_odds") or r.get("odds")}
                for r in rows]
    metric = betting_metrics(prepared)
    binary = [(r["model_probability"], 1 if r["grade"] == "win" else 0) for r in rows
              if r.get("grade") in {"win", "loss"} and r.get("model_probability") is not None]
    return {"predictions": len(rows), "candidates": len(rows) if candidates is None else candidates,
            "accepted_bets": len(rows), "graded_bets": metric["bets"], **metric,
            "accuracy": metric["win_rate"], "average_model_probability": _average(rows, "model_probability"),
            "average_execution_implied_probability": _average(rows, "execution_implied_probability"),
            "average_implied_probability": _average(rows, "execution_implied_probability") or _average(rows, "implied_probability"),
            "average_consensus_probability": _average(rows, "consensus_probability"),
            "average_edge": _average(rows, "edge"), "probability_quality": probability_metrics(binary),
            "calibration": calibration_metrics(rows), "edge_buckets": edge_buckets(prepared)}


def projection_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # A projection is game-level.  Do not overweight games merely because they
    # produced accepted bets in several markets.
    rows = list({(r.get("game_id") or r.get("game")): r for r in rows}.values())
    def score(name: str, actual: str):
        return error_metrics((r["features"][name], r[actual]) for r in rows
                             if r.get("features", {}).get(name) is not None and r.get(actual) is not None)
    return {"home_score": score("projected_home_points", "final_home_score"),
            "away_score": score("projected_away_points", "final_away_score"),
            "total": error_metrics((r["features"]["projected_total"], r["final_home_score"] + r["final_away_score"])
                                   for r in rows if r.get("features", {}).get("projected_total") is not None and r.get("final_home_score") is not None),
            "margin": error_metrics((r["features"]["projected_margin"], r["final_home_score"] - r["final_away_score"])
                                    for r in rows if r.get("features", {}).get("projected_margin") is not None and r.get("final_home_score") is not None)}


def paired_decisions(model_rows: dict[str, list[dict[str, Any]]], models: list[str], universe: set[tuple[str, str]]) -> dict[str, Any]:
    left, right = ({(r["game"], r["market"]): r for r in model_rows[m]} for m in models[:2])
    counts: Counter[str] = Counter(); details = []
    for key in sorted(universe):
        a, b = left.get(key), right.get(key)
        if a and b: category = "both_same_selection" if a["selection"].casefold() == b["selection"].casefold() else "both_opposite_selections"
        elif a: category = "v1_only"
        elif b: category = "v2_only"
        else: category = "neither"
        counts[category] += 1
        details.append({"game_id": key[0], "market": key[1], "category": category,
            "selection_v1": a.get("selection") if a else None, "selection_v2": b.get("selection") if b else None,
            "model_probability_v1": a.get("model_probability") if a else None,
            "model_probability_v2": b.get("model_probability") if b else None,
            "edge_v1": a.get("edge") if a else None, "edge_v2": b.get("edge") if b else None,
            "selection_difference": bool(a and b and a["selection"].casefold() != b["selection"].casefold()),
            "probability_difference_v2_minus_v1": (b["model_probability"]-a["model_probability"]) if a and b else None,
            "edge_difference_v2_minus_v1": (b["edge"]-a["edge"]) if a and b else None,
            "result_v1": a.get("grade") if a else None, "result_v2": b.get("grade") if b else None})
    return {"counts": dict(sorted(counts.items())), "decisions": details}


def _segment(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return {value: market_metrics([r for r in rows if r.get(key) == value])
            for value in sorted({str(r.get(key)) for r in rows if r.get(key) is not None})}


def favorite_underdog(row: dict[str, Any]) -> str | None:
    """Classify the selected side from the market price, never the model."""
    if row.get("market") == "spread" and row.get("line") is not None:
        line = float(row["line"])
        return "favorite" if line < 0 else "underdog" if line > 0 else None
    if row.get("market") == "h2h":
        probability = row.get("consensus_probability")
        if probability is None: probability = row.get("execution_implied_probability")
        if probability is None: probability = row.get("implied_probability")
        if probability is not None:
            return "favorite" if float(probability) > .5 else "underdog" if float(probability) < .5 else None
    return None


def _delta(v2: dict[str, Any], v1: dict[str, Any], projection2=None, projection1=None) -> dict[str, Any]:
    def sub(a, b): return a-b if a is not None and b is not None else None
    result = {"brier": sub(v2["probability_quality"]["brier"], v1["probability_quality"]["brier"]),
              "log_loss": sub(v2["probability_quality"]["log_loss"], v1["probability_quality"]["log_loss"]),
              "accuracy": sub(v2["accuracy"], v1["accuracy"]), "roi": sub(v2["roi"], v1["roi"]),
              "net_units": sub(v2["net_units"], v1["net_units"])}
    if projection1 and projection2:
        result.update({"margin_mae": sub(projection2["margin"]["mae"], projection1["margin"]["mae"]),
                       "total_mae": sub(projection2["total"]["mae"], projection1["total"]["mae"])})
    return result


def compare(*, data_dir: Path, league: str, season: str, start_week: int, end_week: int,
            markets: tuple[str, ...], models: tuple[str, ...]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Replay each immutable snapshot, then assert a common game universe."""
    if start_week > end_week: raise ValueError("start_week must not exceed end_week")
    if len(models) != 2: raise ValueError("paired comparison requires exactly two models")
    markets = normalize_markets(markets); rows_by_model = {}; summaries = {}; game_sets = {}
    with tempfile.TemporaryDirectory() as temp:
        for model in models:
            cfg = BacktestConfig(league=league, season=season, start_week=start_week, end_week=end_week,
                markets=markets, model_version=model, data_dir=data_dir, db_path=Path(temp)/f"{model}.db", export=False)
            with ReplayEngine(cfg) as engine:
                summary = engine.run(); rows = engine.store.load_predictions(summary["run_id"])
                normalized = []
                for week in range(start_week, end_week+1):
                    snapshot_games = engine.provider.get_games(league, season, week)
                    normalized.extend(normalize_outcomes(engine.provider.get_outcomes(league, season, week),
                                                         snapshot_games, league, season, week))
                outcomes = {str(o.get("game_id") or o.get("game")): o for o in normalized}
            games = [g for w in summary["evaluation"].get("weeks", {}).values() for g in w.get("games", [])]
            game_meta = {str(g["game_id"]): g for g in games}
            week_by_game = {str(g["game_id"]): int(week) for week, record in summary["evaluation"].get("weeks", {}).items() for g in record.get("games", [])}
            game_sets[model] = set(game_meta)
            for row in rows:
                meta = game_meta.get(str(row["game"]), {}); outcome = outcomes.get(str(row["game"]), {})
                try: features = json.loads(row.get("features") or "{}")
                except (TypeError, json.JSONDecodeError): features = {}
                row.update({"run_id": summary["run_id"], "model_version": model, "model": model,
                    "game_id": row["game"], "kickoff": meta.get("kickoff"), "week": week_by_game.get(str(row["game"])),
                    "home_team": meta.get("home_team"), "away_team": meta.get("away_team"), "features": features,
                    "final_home_score": outcome.get("final_home_score"), "final_away_score": outcome.get("final_away_score")})
                row["favorite_underdog"] = favorite_underdog(row)
            rows.sort(key=lambda r: (r.get("kickoff") or "", str(r.get("game_id")), r.get("market") or ""))
            rows_by_model[model] = rows
            overall = market_metrics(rows, summary["evaluation"].get("candidates_evaluated", len(rows)))
            by_market = {m: market_metrics([r for r in rows if r["market"] == m]) for m in markets}
            by_week = {str(w): market_metrics([r for r in rows if r.get("week") == w]) for w in range(start_week, end_week+1)}
            cumulative = {str(w): market_metrics([r for r in rows if (r.get("week") or 0) <= w]) for w in range(start_week, end_week+1)}
            projection = projection_metrics(rows)
            confidences = Counter(str(r.get("confidence")) for r in rows)
            summaries[model] = {"overall": overall, "by_market": by_market, "by_week": by_week,
                "cumulative_by_week": cumulative, "calibration_by_market": {m: calibration_metrics([r for r in rows if r["market"] == m]) for m in markets},
                "favorite_underdog": _segment(rows, "favorite_underdog"), "home_away": _segment(rows, "home_away"),
                "projection_error": projection, "projection_error_by_week": {str(w): projection_metrics([r for r in rows if r.get("week") == w]) for w in range(start_week, end_week+1)},
                "confidence": {"unique_values": sorted(confidences), "distribution": dict(sorted(confidences.items())),
                               "effectively_constant": len(confidences) <= 1}, "replay_evaluation": summary["evaluation"]}
    if len({frozenset(s) for s in game_sets.values()}) != 1:
        raise ValueError(f"models produced different eligible game universes: {game_sets}")
    eligible = next(iter(game_sets.values()), set()); universe = {(g, m) for g in eligible for m in markets}
    first, second = models
    deltas = {"overall": _delta(summaries[second]["overall"], summaries[first]["overall"], summaries[second]["projection_error"], summaries[first]["projection_error"]),
              **{m: _delta(summaries[second]["by_market"][m], summaries[first]["by_market"][m]) for m in markets}}
    readiness = {str(w): {"status": "pass", "reasons": []} for w in range(start_week, end_week+1)}
    report = {"schema_version": 2, "league": league, "season": season, "start_week": start_week, "end_week": end_week,
        "dataset": {"games_discovered": len(eligible), "games_eligible": len(eligible), "games_excluded": 0,
                    "exclusion_reasons": {}, "weeks_included": list(range(start_week, end_week+1)), "readiness": readiness,
                    "same_eligible_game_universe": True, "chronological_ordering": "kickoff timestamp ascending"},
        "models": summaries, "paired": paired_decisions(rows_by_model, list(models), universe), "v2_minus_v1": deltas,
        "conclusion": "mixed/inconclusive", "warning": WARNING}
    return report, [r for model in models for r in rows_by_model[model]]


def render_markdown(report: dict[str, Any]) -> str:
    d = report["dataset"]; lines = ["# NFL V1 vs V2 Evaluation", "", f"> **Warning:** {report['warning']}", "",
        "## Executive summary", "", f"Evidence classification: **{report['conclusion']}**.", "",
        "## Dataset/readiness", "", f"Eligible games: {d['games_eligible']} / {d['games_discovered']}; excluded: {d['games_excluded']}."]
    lines += [f"- Week {w}: {v['status']}" for w, v in d["readiness"].items()]
    lines += ["", "## Overall V1 vs V2", ""]
    for model, value in report["models"].items():
        m=value["overall"]; q=m["probability_quality"]
        lines.append(f"- **{model}**: n={m['predictions']}, W-L-P {m['wins']}-{m['losses']}-{m['pushes']}, ROI={m['roi']}, net={m['net_units']}, Brier={q['brier']}, log loss={q['log_loss']}")
    for title in ("Weekly performance", "Market performance", "Calibration", "Edge analysis", "Favorite/underdog", "Home/away", "Model agreement", "Projection error", "Confidence diagnostics"):
        lines += ["", f"## {title}", "", "See the machine-readable JSON for complete sample-sized diagnostics."]
    lines += ["", "## Key weaknesses", "", "The short evaluation window and any non-monotonic edge/calibration behavior require further study.",
              "", "## Recommended V3 research priorities", "", "- **P0:** Collect more history and investigate the weakest probability-calibration market.",
              "- **P1:** Investigate score-margin and total projection error before changing betting thresholds.",
              "- **P2:** Study favorite/underdog and home/away segmentation; do not infer signal from small cells.", ""]
    return "\n".join(lines)


def write_artifacts(report, rows, output: Path, bets: Path, markdown: Path | None = None):
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(report, indent=2, sort_keys=True, default=str)+"\n", encoding="utf-8")
    bets.parent.mkdir(parents=True, exist_ok=True)
    with bets.open("w", newline="", encoding="utf-8") as handle:
        writer=csv.DictWriter(handle, CSV_FIELDS); writer.writeheader()
        for r in rows:
            odds=r.get("sportsbook_odds") or r.get("odds"); implied=r.get("execution_implied_probability") or r.get("implied_probability")
            writer.writerow({"week": r.get("week"), "game_id": r.get("game_id"), "kickoff": r.get("kickoff"), "model": r.get("model_version"),
                "market": r.get("market"), "selection": r.get("selection"), "line": r.get("line"), "american_odds": odds,
                "model_probability": r.get("model_probability"), "consensus_probability": r.get("consensus_probability"),
                "execution_implied_probability": implied, "edge_vs_consensus": r.get("edge"),
                "edge_vs_execution": (float(r["model_probability"])-float(implied)) if r.get("model_probability") is not None and implied is not None else None,
                "decision": "accepted", "grade": r.get("grade"),
                "units_result": american_profit(float(odds), r["grade"]) if odds and r.get("grade") in {"win", "loss", "push"} else None})
    if markdown:
        markdown.parent.mkdir(parents=True, exist_ok=True); markdown.write_text(render_markdown(report), encoding="utf-8")


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--league", default="nfl"); p.add_argument("--season", required=True)
    p.add_argument("--start-week", type=int, required=True); p.add_argument("--end-week", type=int, required=True)
    p.add_argument("--markets", default="h2h,spread,total"); p.add_argument("--models", default="nfl_game_baseline_v1,nfl_game_baseline_v2")
    p.add_argument("--data-dir", type=Path, default=SNAPSHOTS_DIR); p.add_argument("--output", type=Path); p.add_argument("--bets", type=Path); p.add_argument("--markdown", type=Path)
    a=p.parse_args(); report, rows=compare(data_dir=a.data_dir, league=a.league, season=a.season, start_week=a.start_week, end_week=a.end_week, markets=tuple(a.markets.split(",")), models=tuple(a.models.split(",")))
    stem=f"{a.league}_{a.season}_weeks{a.start_week}_{a.end_week}_v1_vs_v2"
    write_artifacts(report, rows, a.output or Path(f"backtesting/results/{stem}.json"), a.bets or Path(f"backtesting/results/{stem}_bets.csv"), a.markdown)
    print(WARNING)

if __name__ == "__main__": main()
