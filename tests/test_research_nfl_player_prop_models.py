import csv
import json
import socket
from types import SimpleNamespace

from backtesting.build_nfl_player_prop_predictions import _persisted_model_features
from backtesting.research_nfl_player_prop_models import (
    FAMILIES, compare_distributions, research, walk_forward_calibration,
    walk_forward_variance,
)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _row(week, index, *, market="receiving_yards"):
    mean=40+index; actual=mean+((-1)**index)*(5+week); probability=.8 if actual>42 else .2
    return {"season":2025,"week":week,"game_id":f"g{week}-{index}","canonical_player_id":f"p{index}",
            "player_name":f"Player {index}","team":"KC" if index%2 else "BUF","opponent":"BUF" if index%2 else "KC",
            "archetype":"RECEIVER","market":market,"line":42.5,"side":"OVER","bookmaker":"book",
            "model_probability":probability,"no_vig_market_probability":.5,"grade":"WIN" if actual>42.5 else "LOSS",
            "profit_units":1 if actual>42.5 else -1,"actual_stat":actual,"simulated_mean":mean,"simulated_stddev":3+index%3,
            "zero_mass":0.02,"player_history_games":5+week,"team_history_rows":20,"league_team_history_rows":100,
            "historical_game_to_game_volatility":4+index%2,"usage_share_volatility":.05+index/1000,
            "opponent_history_mean":mean-2,"recent_form_delta":index/10,"implied_team_total":24,
            "team_spread":-2.5,"game_total":47,"projected_team_points":24.75,"projected_opponent_points":22.25,
            "home_away":"HOME" if index%2 else "AWAY","favorite_status":"FAVORITE"}


def test_distribution_comparison_contains_every_requested_family():
    rows=[_row(1,index) for index in range(12)]
    report=compare_distributions(rows)
    assert {row["family"] for row in report} == set(FAMILIES)
    assert all(row["count"] == 12 for row in report)
    assert all(row["average_negative_log_likelihood"] >= 0 for row in report)
    assert min(row["composite_rank"] for row in report) == 2


def test_walk_forward_variance_calibration_and_permutation_importance():
    rows=[_row(week,index) for week in (1,2,3) for index in range(12)]
    variance,importance=walk_forward_variance(rows,1729,min_train_rows=8,min_test_rows=4)
    calibration=walk_forward_calibration(rows,1729,min_train_rows=8,min_test_rows=4)
    assert variance["status"] == "COMPLETE"
    assert variance["folds"][0]["train_weeks"] == [1,2]
    assert importance and {row["feature"] for row in importance} >= {"simulated_mean","recent_form_delta"}
    assert calibration["status"] == "COMPLETE"
    assert {row["method"] for row in calibration["folds"]} == {"isotonic","beta"}
    assert all(row["test_week"] == 3 for row in calibration["folds"])


def test_persisted_model_features_are_pregame_history_only():
    game={"home_team":"KC","away_team":"BUF"}
    history=[{"game_id":"h1","team":"KC","opponent":"BUF","receiving_yards":20,"data_as_of":"2025-01-01"},
             {"game_id":"h2","team":"KC","opponent":"DEN","receiving_yards":40,"data_as_of":"2025-01-02"}]
    result=SimpleNamespace(assumptions={"player_usage":[{"player_name":"Player","team":"KC","recent_participation_rate":1,
        "receiving_yard_share_proxy":.3}]})
    features=_persisted_model_features(game,SimpleNamespace(home_points=24,away_points=21),result,history,history,"Player","KC","receiving_yards")
    assert features["historical_market_mean"] == 30
    assert features["historical_game_to_game_volatility"] == 10
    assert features["recent_form_delta"] == 0
    assert features["opponent_history_mean"] == 20
    assert features["receiving_yard_share_proxy"] == .3
    assert "actual" not in features and "outcome" not in features


def _research_fixture(tmp_path):
    snapshots=tmp_path/"snapshots"; week=snapshots/"nfl/2025/week_01"; results=tmp_path/"season"; results.mkdir(parents=True)
    predictions=[]; opportunities=[]; identities=[]; games=[]; odds=[]
    for index in range(6):
        base=_row(1,index); gid=base["game_id"]; team=base["team"]; opponent=base["opponent"]
        games.append({"game_id":gid,"season":2025,"week":1,"home_team":team,"away_team":opponent})
        odds += [{"game_id":gid,"market":"totals","line":47,"selection":"Over"},
                 {"game_id":gid,"market":"spreads","line":-3,"selection":team},
                 {"game_id":gid,"market":"spreads","line":3,"selection":opponent}]
        identities.append({"game_id":gid,"canonical_player_id":base["canonical_player_id"],"position":"WR"})
        summary={"mean":base["simulated_mean"],"median":base["simulated_mean"],"standard_deviation":base["simulated_stddev"],
                 "minimum":0,"maximum":100,"zero_mass":.02,"unique_values":100,"quantiles":{"p05":10,"p95":80}}
        for side,prob,grade,profit in (("OVER",base["model_probability"],base["grade"],base["profit_units"]),
                                      ("UNDER",1-base["model_probability"],"LOSS" if base["grade"]=="WIN" else "WIN",-base["profit_units"])):
            predictions.append({"season":2025,"week":1,"game_id":gid,"canonical_player_id":base["canonical_player_id"],
                "player_name":base["player_name"],"team":team,"market":base["market"],"line":base["line"],"side":side,
                "model_probability":prob,"distribution_summary":summary,"provenance":{"player_history_games":6,"team_history_rows":20,"league_team_history_rows":100},
                "model_features":{"historical_game_to_game_volatility":5,"usage_share_volatility":.1,"opponent_history_mean":38,"recent_form_delta":2}})
            opportunities.append({"season":2025,"week":1,"game_id":gid,"canonical_player_id":base["canonical_player_id"],
                "player_name":base["player_name"],"team":team,"market":base["market"],"line":base["line"],"side":side,"bookmaker":"book",
                "model_probability":prob,"no_vig_market_probability":.5,"grade":grade,"outcome":base["actual_stat"],"edge":.2,"profit_units":profit,"american_odds":100})
    for name,value in (("player_prop_predictions.json",predictions),("player_identities.json",identities),("games.json",games),("odds.json",odds)):
        _write_json(week/name,value)
    fields=sorted({key for row in opportunities for key in row})
    with (results/"opportunity_rows.csv").open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=fields); writer.writeheader(); writer.writerows(opportunities)
    return snapshots,results


def test_research_command_is_offline_artifact_first_and_deterministic(tmp_path,monkeypatch):
    snapshots,results=_research_fixture(tmp_path); monkeypatch.setattr(socket,"create_connection",lambda *a,**k: (_ for _ in ()).throw(AssertionError("network forbidden")))
    outputs=[tmp_path/"out1",tmp_path/"out2"]
    reports=[research(season=2025,start_week=1,end_week=3,snapshot_root=snapshots,season_results_dir=results,
                      output_dir=output,min_train_rows=4,min_test_rows=2,min_segment_size=2) for output in outputs]
    first=reports[0]
    assert first["variance_model_report.json"]["status"] == "INSUFFICIENT_HISTORY"
    assert first["calibration_report.json"]["status"] == "INSUFFICIENT_HISTORY"
    assert len(first["distribution_comparison.json"]) == len(FAMILIES)
    assert {row["dimension"] for row in first["residual_clusters.json"]} >= {"team","bookmaker","home_away","favorite_status"}
    availability={row["feature"]:row for row in first["feature_availability.json"]}
    assert availability["usage_share_volatility"]["status"] == "AVAILABLE"
    assert availability["projected_pace"]["status"] == "NOT_PERSISTED"
    assert first["research_manifest.json"]["network_contacted"] is False
    assert (outputs[0]/"research_manifest.json").read_bytes() == (outputs[1]/"research_manifest.json").read_bytes()
