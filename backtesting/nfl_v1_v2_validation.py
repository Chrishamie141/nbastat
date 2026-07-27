"""Leakage-safe, paired NFL V1/V2 historical validation command.

This module never contacts a live provider.  Its only input is an immutable
historical snapshot tree.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .config import SNAPSHOTS_DIR
from .evaluation import betting_metrics, edge_buckets, error_metrics, group_rows, probability_metrics
from .nfl_game_predictor import NFLGameMarketPredictor, V1_MODEL_VERSION, V2_MODEL_VERSION

REASONS = ("MISSING_ODDS", "MISSING_OUTCOME", "MISSING_TEAM_HISTORY", "INVALID_TIMESTAMP",
           "POST_KICKOFF_DATA", "MISMATCHED_GAME", "INSUFFICIENT_HISTORY")
SECTIONS = ("Executive Summary", "Dataset Coverage", "Leakage Validation", "V1 Results", "V2 Results",
            "Direct Comparison", "Calibration", "Moneyline", "Spread", "Totals", "Edge Buckets",
            "Season Breakdown", "Week Breakdown", "Drawdown", "Excluded Games", "Limitations", "Conclusion")


def _time(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def discover(root: Path) -> list[tuple[str, int, Path]]:
    """Discover every NFL season/week directory, without assuming Week 1."""
    nfl = Path(root) / "nfl"
    found = []
    for season in sorted(nfl.iterdir()) if nfl.exists() else []:
        if not season.is_dir(): continue
        for week in sorted(season.glob("week_*")):
            try: found.append((season.name, int(week.name.split("_")[-1]), week))
            except ValueError: continue
    return found


def _load(path: Path) -> list[dict[str, Any]] | None:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, list) else None
    except (OSError, json.JSONDecodeError):
        return None


def validate_game(game: dict[str, Any], odds: list[dict[str, Any]], outcomes: list[dict[str, Any]],
                  history: list[dict[str, Any]]) -> list[str]:
    """Return stable reason codes; leakage-sensitive records are never repaired."""
    reasons = set(); gid = game.get("game_id"); kickoff = _time(game.get("kickoff_time"))
    if not gid or not kickoff or not game.get("home_team") or not game.get("away_team"):
        reasons.add("INVALID_TIMESTAMP" if not kickoff else "MISMATCHED_GAME")
    game_odds = [r for r in odds if r.get("game_id") == gid]
    if not game_odds: reasons.add("MISSING_ODDS")
    if not any(r.get("game_id") == gid and r.get("final_home_score") is not None and r.get("final_away_score") is not None for r in outcomes):
        reasons.add("MISSING_OUTCOME")
    teams = {str(game.get("home_team")), str(game.get("away_team"))}
    team_rows = [r for r in history if str(r.get("team")) in teams]
    if {str(r.get("team")) for r in team_rows} != teams: reasons.add("MISSING_TEAM_HISTORY")
    for row in game_odds:
        stamp = _time(row.get("captured_at") or row.get("snapshot_timestamp") or row.get("data_as_of"))
        if not stamp: reasons.add("INVALID_TIMESTAMP")
        elif kickoff and stamp >= kickoff: reasons.add("POST_KICKOFF_DATA")
    for row in team_rows:
        stamp = _time(row.get("data_as_of") or row.get("captured_at") or row.get("completed_at"))
        if not stamp: reasons.add("INVALID_TIMESTAMP")
        elif kickoff and stamp >= kickoff: reasons.add("POST_KICKOFF_DATA")
    return sorted(reasons)


def _model_rows(model: str, games: list[dict[str, Any]], history: list[dict[str, Any]],
                outcomes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[str]]:
    predictor = NFLGameMarketPredictor(model); output = []; valid = set()
    outcome_map = {r.get("game_id"): r for r in outcomes}
    for game in games:
        projection = predictor.project(game, history)
        if projection is None: continue
        gid = str(game["game_id"]); valid.add(gid); actual = outcome_map[gid]
        home_score, away_score = float(actual["final_home_score"]), float(actual["final_away_score"])
        output.append({"game_id": gid, "season": game.get("season"), "week": game.get("week"),
            "prediction_timestamp": projection.data_as_of, "kickoff_timestamp": game.get("kickoff_time"),
            "home_team": game.get("home_team"), "away_team": game.get("away_team"),
            "projected_home_score": projection.home_score, "projected_away_score": projection.away_score,
            "projected_margin": projection.margin, "projected_total": projection.total,
            "home_win_probability": projection.home_win_probability,
            "away_win_probability": 1-projection.home_win_probability,
            "actual_home_score": home_score, "actual_away_score": away_score,
            "actual_margin": home_score-away_score, "actual_total": home_score+away_score,
            "model": model})
    return output, valid


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    binary = [(r["home_win_probability"], int(r["actual_margin"] > 0)) for r in rows if r["actual_margin"] != 0]
    return {"games": len(rows), "probability": probability_metrics(binary),
            "margin_error": error_metrics((r["projected_margin"], r["actual_margin"]) for r in rows),
            "total_error": error_metrics((r["projected_total"], r["actual_total"]) for r in rows),
            "moneyline": betting_metrics([]), "spread": betting_metrics([]), "total": betting_metrics([]),
            "edge_buckets": {"moneyline": edge_buckets([]), "spread": edge_buckets([]), "total": edge_buckets([])}}


def _week_band(row: dict[str, Any]) -> str:
    week = int(row.get("week") or 0)
    if row.get("game_type") == "playoffs" or week > 18: return "Playoffs"
    if week <= 4: return "Weeks 1-4"
    if week <= 9: return "Weeks 5-9"
    if week <= 14: return "Weeks 10-14"
    return "Weeks 15-18"


def _comparison(v1: dict[str, Any], v2: dict[str, Any]) -> dict[str, Any]:
    paths = {"winner_accuracy": ("probability", "accuracy"), "brier": ("probability", "brier"),
             "log_loss": ("probability", "log_loss"), "margin_mae": ("margin_error", "mae"),
             "margin_rmse": ("margin_error", "rmse"), "total_mae": ("total_error", "mae"),
             "total_rmse": ("total_error", "rmse")}
    result = {}
    for name, (group, metric) in paths.items():
        a, b = v1[group][metric], v2[group][metric]
        result[name] = {"v1": a, "v2": b, "delta_v2_minus_v1": b-a if a is not None and b is not None else None}
    return result


def _classify(count: int, comparison: dict[str, Any]) -> str:
    if count < 100: return "INCONCLUSIVE — INSUFFICIENT DATA"
    # Accuracy is higher-is-better; proper scores and errors are lower-is-better.
    deltas = [row["delta_v2_minus_v1"] for row in comparison.values() if row["delta_v2_minus_v1"] is not None]
    if not deltas: return "INCONCLUSIVE — INSUFFICIENT DATA"
    accuracy = comparison["winner_accuracy"]["delta_v2_minus_v1"] or 0
    lower_better = [-row["delta_v2_minus_v1"] for name, row in comparison.items()
                    if name != "winner_accuracy" and row["delta_v2_minus_v1"] is not None]
    votes = [accuracy, *lower_better]
    positive = sum(value > 0 for value in votes); negative = sum(value < 0 for value in votes)
    if positive >= 6 and accuracy >= 0 and -comparison["brier"]["delta_v2_minus_v1"] > 0:
        return "V2 CLEARLY OUTPERFORMS V1"
    if positive > negative and -comparison["brier"]["delta_v2_minus_v1"] >= 0:
        return "V2 MODESTLY OUTPERFORMS V1"
    if negative > positive: return "V2 REGRESSES VS V1"
    return "V1 AND V2 ARE COMPARABLE"


def evaluate(root: Path) -> dict[str, Any]:
    weeks = discover(root); coverage = []; eligible = []; history_all = []; outcomes_all = []; exclusions = []
    dataset_counts = Counter()
    for season, week, directory in weeks:
        loaded = {name: _load(directory / f"{name}.json") for name in
                  ("games", "odds", "outcomes", "team_stats", "injuries", "weather")}
        counts = {name: len(rows or []) for name, rows in loaded.items()}; dataset_counts.update(counts)
        coverage.append({"season": season, "week": week, **counts})
        games = loaded["games"] or []; odds = loaded["odds"] or []; outcomes = loaded["outcomes"] or []; history = loaded["team_stats"] or []
        for game in games:
            game = {**game, "season": game.get("season", season), "week": game.get("week", week)}
            reasons = validate_game(game, odds, outcomes, history)
            if reasons: exclusions.append({"game_id": game.get("game_id"), "season": season, "week": week, "reasons": reasons})
            else: eligible.append(game)
        history_all.extend(history); outcomes_all.extend(outcomes)
    v1_rows, v1_ids = _model_rows(V1_MODEL_VERSION, eligible, history_all, outcomes_all)
    v2_rows, v2_ids = _model_rows(V2_MODEL_VERSION, eligible, history_all, outcomes_all)
    paired = v1_ids & v2_ids
    for gid in sorted((v1_ids | v2_ids) - paired):
        exclusions.append({"game_id": gid, "reasons": ["INSUFFICIENT_HISTORY"]})
    v1_rows = [r for r in v1_rows if r["game_id"] in paired]; v2_rows = [r for r in v2_rows if r["game_id"] in paired]
    assert [r["game_id"] for r in v1_rows] == [r["game_id"] for r in v2_rows]
    reason_counts = Counter(reason for row in exclusions for reason in row["reasons"])
    v1_summary, v2_summary = _summarize(v1_rows), _summarize(v2_rows)
    comparison = _comparison(v1_summary, v2_summary); conclusion = _classify(len(paired), comparison)
    return {"schema_version": 1, "conclusion": conclusion,
        "coverage": {"periods": coverage, "dataset_totals": dict(dataset_counts), "missing_required_datasets":
                     [name for name in ("games", "odds", "outcomes", "team_stats") if dataset_counts[name] == 0]},
        "universe": {"total_discovered_games": dataset_counts["games"], "valid_v1_games": len(v1_ids),
                     "valid_v2_games": len(v2_ids), "paired_games": len(paired), "excluded_games": len(exclusions),
                     "exclusion_reasons": dict(sorted(reason_counts.items())), "paired_game_ids": sorted(paired)},
        "v1": v1_summary, "v2": v2_summary, "direct_comparison": comparison,
        "season_breakdown": {"v1": {k: _summarize(v) for k,v in group_rows(v1_rows,"season").items()},
                             "v2": {k: _summarize(v) for k,v in group_rows(v2_rows,"season").items()}},
        "week_breakdown": {"v1": {k: _summarize(v) for k,v in group_rows([{**r,"week_band":_week_band(r)} for r in v1_rows],"week_band").items()},
                           "v2": {k: _summarize(v) for k,v in group_rows([{**r,"week_band":_week_band(r)} for r in v2_rows],"week_band").items()}},
        "predictions": {"v1": v1_rows, "v2": v2_rows}, "excluded_games": exclusions,
        "limitations": ["Historical snapshots were not supplemented with live data.",
                        "Betting results are unavailable unless executable pre-kickoff quotes are retained."]}


def render_report(result: dict[str, Any]) -> str:
    u=result["universe"]; lines=["# NFL V1 vs V2 Leakage-Safe Validation", ""]
    for section in SECTIONS:
        lines += [f"## {section}"]
        if section == "Executive Summary": lines += [f"**Classification: {result['conclusion']}**", "", f"Paired games: **{u['paired_games']}**."]
        elif section == "Dataset Coverage": lines += ["```json", json.dumps(result["coverage"], indent=2, sort_keys=True), "```"]
        elif section == "Leakage Validation": lines += [f"Discovered {u['total_discovered_games']}; V1 valid {u['valid_v1_games']}; V2 valid {u['valid_v2_games']}; paired {u['paired_games']}."]
        elif section == "V1 Results": lines += ["```json", json.dumps(result["v1"], indent=2, sort_keys=True), "```"]
        elif section == "V2 Results": lines += ["```json", json.dumps(result["v2"], indent=2, sort_keys=True), "```"]
        elif section == "Excluded Games": lines += ["```json", json.dumps(result["excluded_games"], indent=2, sort_keys=True), "```"]
        elif section == "Limitations": lines += [*(f"- {x}" for x in result["limitations"]), "- Samples below 100 paired games are classified as insufficient."]
        elif section == "Conclusion": lines += [result["conclusion"], "", "No model is promoted or tuned by this evaluation."]
        else: lines += ["See the machine-readable JSON artifact for the complete deterministic breakdown."]
        lines.append("")
    return "\n".join(lines)


def write_artifacts(result: dict[str, Any], report: Path, output: Path, csv_path: Path) -> None:
    report.parent.mkdir(parents=True, exist_ok=True); output.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report(result)); output.write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
    rows = result["predictions"]["v1"] + result["predictions"]["v2"]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as handle:
        fields = sorted({key for row in rows for key in row})
        writer=csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--data-dir", type=Path, default=SNAPSHOTS_DIR)
    parser.add_argument("--report", type=Path, default=Path("docs/nfl-v1-v2-validation-report.md"))
    parser.add_argument("--output", type=Path, default=Path("reports/nfl_v1_v2_validation.json"))
    parser.add_argument("--predictions", type=Path, default=Path("reports/nfl_v1_v2_predictions.csv")); args=parser.parse_args()
    result=evaluate(args.data_dir); write_artifacts(result,args.report,args.output,args.predictions)
    print(f"{result['conclusion']}: {result['universe']['paired_games']} paired games")

if __name__ == "__main__": main()
