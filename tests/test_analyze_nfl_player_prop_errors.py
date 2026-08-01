import csv
import json
import socket

from backtesting.analyze_nfl_player_prop_errors import analyze


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(tmp_path):
    snapshots=tmp_path/"snapshots"; week=snapshots/"nfl/2025/week_01"; results=tmp_path/"season"
    identities=[]; predictions=[]; opportunities=[]
    specs=[
        ("p1","Alpha QB","KC","passing_yards",250.5,310,15,.99,180,"LOSS",.30,-1,"QB"),
        ("p2","Beta WR","BUF","receiving_yards",60.5,62,35,.55,90,"WIN",.04,1,"WR"),
        ("p3","Gamma RB","KC","rushing_yards",70.5,90,8,.95,40,"LOSS",.25,-1,"RB"),
        ("p4","Delta WR","BUF","receiving_yards",40.5,38,30,.45,20,"WIN",.03,1,"WR"),
    ]
    for index,(pid,name,team,market,line,mean,sd,over,actual,grade,edge,profit,position) in enumerate(specs):
        game=f"g{index}"; identities.append({"game_id":game,"canonical_player_id":pid,"position":position})
        summary={"count":10000,"minimum":max(0,mean-2*sd),"maximum":mean+2*sd,"mean":mean,"median":mean,
                 "standard_deviation":sd,"unique_values":100,"zero_mass":0,"quantiles":{"p05":mean-1.64*sd,"p95":mean+1.64*sd}}
        for side,prob,side_grade in (("OVER",over,grade),("UNDER",1-over,"WIN" if grade=="LOSS" else "LOSS")):
            predictions.append({"season":2025,"week":1,"game_id":game,"canonical_player_id":pid,"player_name":name,"team":team,
                                "market":market,"line":line,"side":side,"model_probability":prob,"distribution_summary":summary,
                                "provenance":{"player_history_games":index+1,"team_history_rows":10,"league_team_history_rows":100}})
            opportunities.append({"season":2025,"week":1,"game_id":game,"canonical_player_id":pid,"player_name":name,"team":team,
                                  "market":market,"line":line,"side":side,"bookmaker":"book","model_probability":prob,
                                  "no_vig_market_probability":.5,"grade":side_grade,"outcome":actual,"edge":edge if side=="OVER" else -edge,
                                  "profit_units":profit if side=="OVER" else (1 if profit<0 else -1),"american_odds":100})
    _write_json(week/"player_prop_predictions.json",predictions); _write_json(week/"player_identities.json",identities)
    results.mkdir(parents=True)
    fields=sorted({key for row in opportunities for key in row})
    with (results/"opportunity_rows.csv").open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=fields); writer.writeheader(); writer.writerows(opportunities)
    return snapshots,results


def test_analysis_answers_all_five_questions_and_is_deterministic(tmp_path, monkeypatch):
    snapshots,results=_fixture(tmp_path); outputs=[tmp_path/"out1",tmp_path/"out2"]
    monkeypatch.setattr(socket,"create_connection",lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError("network forbidden")))
    reports=[]
    reports.append(analyze(season=2025,start_week=1,end_week=2,snapshot_root=snapshots,
                           season_results_dir=results,output_dir=outputs[0],top_n=3,min_segment_size=1))
    prediction_path=snapshots/"nfl/2025/week_01/player_prop_predictions.json"
    predictions=json.loads(prediction_path.read_text(encoding="utf-8")); _write_json(prediction_path,list(reversed(predictions)))
    opportunity_path=results/"opportunity_rows.csv"; rows=list(csv.DictReader(opportunity_path.open(encoding="utf-8")))
    with opportunity_path.open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(reversed(rows))
    reports.append(analyze(season=2025,start_week=1,end_week=2,snapshot_root=snapshots,
                           season_results_dir=results,output_dir=outputs[1],top_n=3,min_segment_size=1))
    summary=reports[0]["error_analysis_summary.json"]
    assert summary["joined_side_forecasts"] == 8
    assert summary["base_opportunities"] == 4
    assert summary["coverage_status"] == "SINGLE_WEEK_ONLY"
    assert summary["evaluated_weeks"] == [1]
    assert summary["answers"]["strongest_extreme_probability_drivers"]
    assert summary["answers"]["currently_overconfident_markets"]
    assert summary["answers"]["poorly_modeled_segments"]
    assert summary["answers"]["poorly_modeled_teams"]
    assert summary["answers"]["poorly_modeled_archetypes"]
    assert summary["answers"]["mean_vs_variance"]
    assert summary["answers"]["largest_roi_loss_contributors"][0]["edge"] == .30
    assert set(row["archetype"] for row in reports[0]["segment_metrics.json"] if "archetype" in row) >= {"QB","RB","WR"}
    assert all(row["consistency_status"] == "INSUFFICIENT_MULTIWEEK_EVIDENCE" for row in reports[0]["market_overconfidence.json"])
    assert {path.name for path in outputs[0].iterdir()} >= {"feature_attribution.json","market_overconfidence.json",
        "segment_metrics.json","mean_variance_diagnostics.json","roi_loss_contributors.json","analysis_manifest.json"}
    assert (outputs[0]/"analysis_manifest.json").read_bytes() == (outputs[1]/"analysis_manifest.json").read_bytes()
    names=reports[0]["analysis_manifest.json"]["artifacts"]
    assert {name:(outputs[0]/name).read_bytes() for name in names} == {name:(outputs[1]/name).read_bytes() for name in names}


def test_conflicting_prediction_key_fails_closed(tmp_path):
    snapshots,results=_fixture(tmp_path)
    path=snapshots/"nfl/2025/week_01/player_prop_predictions.json"
    rows=json.loads(path.read_text(encoding="utf-8")); rows.append({**rows[0],"model_probability":.2}); _write_json(path,rows)
    try:
        analyze(season=2025,start_week=1,end_week=1,snapshot_root=snapshots,
                season_results_dir=results,output_dir=tmp_path/"out")
    except ValueError as exc:
        assert "conflicting prediction rows" in str(exc)
    else:
        raise AssertionError("conflicting canonical prediction key must fail closed")
