"""Offline, leakage-safe NFL player-prop distribution and calibration research.

Distribution families are scored against frozen means and variances. Learned
variance and calibration models use expanding walk-forward folds only; they do
not fit when there are fewer than two prior evaluated weeks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
from scipy import stats

from .analyze_nfl_player_prop_errors import (
    _base_key, _metric, _one_per_base, _semantic_hash, _write_csv, _write_json,
    load_joined_analysis_rows,
)
from .config import SNAPSHOTS_DIR
from .game_matching import normalize_team
from .snapshots import snapshot_week_dir


FAMILIES=("normal","lognormal","gamma","poisson","negative_binomial",
          "zero_inflated_poisson","zero_inflated_negative_binomial")
JSON_ARTIFACTS=("research_summary.json","distribution_comparison.json","variance_model_report.json",
                "calibration_report.json","permutation_importance.json","residual_clusters.json",
                "feature_availability.json")
CSV_ARTIFACTS=("distribution_comparison.csv","permutation_importance.csv","residual_clusters.csv")


def _hash(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def _clip_probability(value: float) -> float: return min(1-1e-12,max(1e-12,float(value)))


def _nb_params(mean: float, variance: float) -> tuple[float,float]:
    variance=max(variance,mean+1e-6); n=max(1e-6,mean*mean/(variance-mean)); return n,n/(n+mean)


def _zero_inflation(mean: float, target_zero: float | None, base: str, variance: float) -> float:
    if target_zero is None or mean<=0: return 0.0
    target=min(.95,max(0.0,target_zero)); low=0.0; high=.95
    def pzero(pi: float) -> float:
        conditional_mean=mean/max(1e-9,1-pi)
        if base=="poisson": zero=math.exp(-conditional_mean)
        else:
            conditional_variance=max(conditional_mean+1e-6,
                (variance-pi*(1-pi)*conditional_mean**2)/max(1e-9,1-pi))
            n,p=_nb_params(conditional_mean,conditional_variance); zero=float(stats.nbinom.pmf(0,n,p))
        return pi+(1-pi)*zero
    if target<=pzero(0): return 0.0
    for _ in range(60):
        middle=(low+high)/2
        if pzero(middle)<target: low=middle
        else: high=middle
    return (low+high)/2


def _distribution_functions(family: str, mean: float, variance: float,
                            zero_mass: float | None) -> tuple[Any,Any,Any,bool]:
    mean=max(0.0,mean); variance=max(1e-6,variance)
    if family=="normal":
        distribution=stats.norm(loc=mean,scale=math.sqrt(variance)); return distribution.cdf,distribution.pdf,distribution.ppf,False
    if family=="lognormal":
        adjusted=max(mean,1e-6); sigma2=math.log1p(variance/(adjusted*adjusted)); sigma=math.sqrt(max(sigma2,1e-9))
        distribution=stats.lognorm(s=sigma,scale=math.exp(math.log(adjusted)-sigma2/2)); return distribution.cdf,distribution.pdf,distribution.ppf,False
    if family=="gamma":
        adjusted=max(mean,1e-6); distribution=stats.gamma(a=max(1e-6,adjusted*adjusted/variance),scale=variance/adjusted)
        return distribution.cdf,distribution.pdf,distribution.ppf,False
    if family=="poisson":
        distribution=stats.poisson(mu=mean); return distribution.cdf,distribution.pmf,distribution.ppf,True
    if family=="negative_binomial":
        n,p=_nb_params(mean,variance); distribution=stats.nbinom(n,p); return distribution.cdf,distribution.pmf,distribution.ppf,True
    base="poisson" if family=="zero_inflated_poisson" else "negative_binomial"
    pi=_zero_inflation(mean,zero_mass,"poisson" if base=="poisson" else "negative_binomial",variance)
    conditional_mean=mean/max(1e-9,1-pi)
    if base=="poisson": distribution=stats.poisson(mu=conditional_mean)
    else:
        conditional_variance=max(conditional_mean+1e-6,
            (variance-pi*(1-pi)*conditional_mean**2)/max(1e-9,1-pi))
        n,p=_nb_params(conditional_mean,conditional_variance); distribution=stats.nbinom(n,p)
    def cdf(x: float) -> float: return pi+(1-pi)*float(distribution.cdf(x)) if x>=0 else 0.0
    def pmf(x: float) -> float: return (pi if x==0 else 0)+(1-pi)*float(distribution.pmf(x))
    def ppf(q: float) -> float:
        if q<=pi+(1-pi)*float(distribution.pmf(0)): return 0.0
        return float(distribution.ppf((q-pi)/(1-pi)))
    return cdf,pmf,ppf,True


def _score_family(row: dict[str,Any], family: str) -> dict[str,Any]:
    mean=float(row["simulated_mean"]); variance=max(1e-6,float(row["simulated_stddev"])**2)
    actual=float(row["actual_stat"]); line=float(row["line"]); zero=row.get("zero_mass")
    cdf,density,ppf,discrete=_distribution_functions(family,mean,variance,None if zero is None else float(zero))
    if discrete:
        likelihood=float(density(round(actual))) if abs(actual-round(actual))<1e-9 else 1e-15
        over=1-float(cdf(math.floor(line))); under=float(cdf(math.ceil(line)-1)); push=float(density(round(line))) if abs(line-round(line))<1e-9 else 0.0
    else:
        lower=actual-.5; upper=actual+.5
        likelihood=max(0.0,float(cdf(upper))-float(cdf(lower)))
        over=1-float(cdf(line)); under=float(cdf(line)); push=0.0
    settled=actual!=line; y=1.0 if actual>line else 0.0
    probability=_clip_probability(over)
    return {"negative_log_likelihood":-math.log(max(likelihood,1e-15)),
            "over_probability":over,"under_probability":under,"push_probability":push,
            "brier":(probability-y)**2 if settled else None,
            "log_loss":-(y*math.log(probability)+(1-y)*math.log(1-probability)) if settled else None,
            "p05_p95_covered":float(ppf(.05))<=actual<=float(ppf(.95))}


def compare_distributions(rows: list[dict[str,Any]]) -> list[dict[str,Any]]:
    grouped: dict[tuple[str,str],list[dict[str,Any]]] = defaultdict(list)
    for row in _one_per_base(rows):
        for family in FAMILIES: grouped[(str(row["market"]),family)].append(_score_family(row,family))
    output=[]
    for (market,family),values in sorted(grouped.items()):
        output.append({"market":market,"family":family,"count":len(values),
                       "average_negative_log_likelihood":sum(row["negative_log_likelihood"] for row in values)/len(values),
                       "brier":sum(row["brier"] for row in values if row["brier"] is not None)/sum(row["brier"] is not None for row in values),
                       "log_loss":sum(row["log_loss"] for row in values if row["log_loss"] is not None)/sum(row["log_loss"] is not None for row in values),
                       "p05_p95_coverage":sum(row["p05_p95_covered"] for row in values)/len(values)})
    for market in sorted({row["market"] for row in output}):
        subset=[row for row in output if row["market"]==market]
        for metric in ("average_negative_log_likelihood","brier"):
            for rank,row in enumerate(sorted(subset,key=lambda item:(item[metric],item["family"])),1): row[f"{metric}_rank"]=rank
        for row in subset: row["composite_rank"]=row["average_negative_log_likelihood_rank"]+row["brier_rank"]
    return sorted(output,key=lambda row:(row["market"],row["composite_rank"],row["family"]))


def _game_context(snapshot_root: Path, season: int, start_week: int, end_week: int) -> tuple[dict[str,dict[str,Any]],list[Path]]:
    context={}; inputs=[]
    for week in range(start_week,end_week+1):
        directory=snapshot_week_dir(snapshot_root,"nfl",season,week); games_path=directory/"games.json"; odds_path=directory/"odds.json"
        games=json.loads(games_path.read_text()) if games_path.exists() else []
        odds=json.loads(odds_path.read_text()) if odds_path.exists() else []
        if games_path.exists(): inputs.append(games_path)
        if odds_path.exists(): inputs.append(odds_path)
        by_game=defaultdict(list)
        for row in odds: by_game[str(row.get("game_id"))].append(row)
        for game in games:
            gid=str(game.get("game_id")); values=by_game[gid]
            totals=[float(row["line"]) for row in values if str(row.get("market")).lower() in {"totals","total"} and row.get("line") is not None]
            spreads=defaultdict(list)
            for row in values:
                if str(row.get("market")).lower() in {"spreads","spread"} and row.get("line") is not None:
                    spreads[normalize_team(row.get("selection") or row.get("outcome"))].append(float(row["line"]))
            context[gid]={"home_team":normalize_team(game.get("home_team")),"away_team":normalize_team(game.get("away_team")),
                          "game_total":median(totals) if totals else None,
                          "spreads":{team:median(lines) for team,lines in sorted(spreads.items())}}
    return context,inputs


def enrich_context(rows: list[dict[str,Any]], snapshot_root: Path, season: int,
                   start_week: int, end_week: int) -> tuple[list[dict[str,Any]],list[Path]]:
    games,inputs=_game_context(snapshot_root,season,start_week,end_week); enriched=[]
    market_lines=defaultdict(list)
    for row in _one_per_base(rows): market_lines[str(row["market"])].append(float(row["line"]))
    boundaries={market:tuple(float(x) for x in np.quantile(lines,(1/3,2/3))) for market,lines in market_lines.items()}
    for original in rows:
        row=dict(original); features=row.pop("persisted_model_features",{}) or {}; row.update({key:value for key,value in features.items() if key not in row})
        game=games.get(str(row.get("game_id")),{}); team=normalize_team(row.get("team")); home=game.get("home_team"); away=game.get("away_team")
        row["home_away"]=row.get("home_away") or ("HOME" if team==home else "AWAY" if team==away else "UNKNOWN")
        row["opponent"]=row.get("opponent") or (away if team==home else home if team==away else "UNKNOWN")
        spread=(game.get("spreads") or {}).get(team); total=game.get("game_total")
        row["team_spread"]=spread; row["game_total"]=total
        row["favorite_status"]="FAVORITE" if spread is not None and spread<0 else "UNDERDOG" if spread is not None and spread>0 else "PICKEM_OR_UNKNOWN"
        row["implied_team_total"]=(total-spread)/2 if total is not None and spread is not None else row.get("projected_team_points")
        q1,q2=boundaries[str(row["market"])]; line=float(row["line"])
        row["line_size"]="LOW" if line<=q1 else "MEDIUM" if line<=q2 else "HIGH"
        implied=row.get("implied_team_total"); row["implied_team_total_bucket"]="UNAVAILABLE" if implied is None else "<20" if implied<20 else "20-23" if implied<23 else "23-26" if implied<26 else ">=26"
        pace=row.get("projected_pace"); row["projected_pace_bucket"]="UNAVAILABLE" if pace is None else "LOW" if pace<61 else "MEDIUM" if pace<67 else "HIGH"
        enriched.append(row)
    return enriched,inputs


def feature_availability(rows: list[dict[str,Any]]) -> list[dict[str,Any]]:
    requested={"historical_game_to_game_volatility":"variance_model","usage_share_volatility":"variance_model",
               "opponent_history_mean":"variance_model","recent_form_delta":"variance_model","opponent":"variance_model_and_error_cluster","team":"error_cluster",
               "archetype":"error_cluster","bookmaker":"error_cluster","line_size":"error_cluster",
               "favorite_status":"error_cluster","home_away":"error_cluster","implied_team_total":"error_cluster",
               "projected_pace":"error_cluster"}
    return [{"feature":name,"purpose":purpose,"rows":len(rows),"available_rows":sum(row.get(name) is not None for row in rows),
             "coverage":sum(row.get(name) is not None for row in rows)/len(rows) if rows else 0,
             "status":"AVAILABLE" if rows and all(row.get(name) is not None for row in rows) else "PARTIAL" if any(row.get(name) is not None for row in rows) else "NOT_PERSISTED"}
            for name,purpose in requested.items()]


def residual_clusters(rows: list[dict[str,Any]], min_segment_size: int) -> list[dict[str,Any]]:
    dimensions=("team","archetype","bookmaker","line_size","favorite_status","home_away",
                "implied_team_total_bucket","projected_pace_bucket")
    output=[]
    for dimension in dimensions:
        groups=defaultdict(list)
        for row in rows: groups[str(row.get(dimension) or "UNAVAILABLE")].append(row)
        for value,values in sorted(groups.items()):
            metric=_metric(values); residuals=[float(row["actual_stat"])-float(row["simulated_mean"]) for row in values]
            output.append({"dimension":dimension,"value":value,"eligible_for_ranking":len(values)>=min_segment_size,
                           **metric,"mean_residual":sum(residuals)/len(residuals),
                           "mean_absolute_residual":sum(abs(value) for value in residuals)/len(residuals)})
    return sorted(output,key=lambda row:(row["dimension"],-(row["model_minus_market_brier"] or -math.inf),row["value"]))


def _gaussian_nll(residual: np.ndarray, variance: np.ndarray) -> float:
    variance=np.maximum(variance,1e-6); return float(np.mean(.5*(np.log(variance)+residual*residual/variance)))


def walk_forward_variance(rows: list[dict[str,Any]], seed: int, min_train_rows: int,
                          min_test_rows: int) -> tuple[dict[str,Any],list[dict[str,Any]]]:
    weeks=sorted({int(row["week"]) for row in rows}); folds=[]; importances=[]
    if len(weeks)<3:
        return {"status":"INSUFFICIENT_HISTORY","reason":"requires at least three evaluated weeks so every test fold has two prior weeks","weeks":weeks,"folds":[]},[]
    from pandas import DataFrame
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.inspection import permutation_importance
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder
    numeric=("simulated_mean","line","player_history_games","team_history_rows","league_team_history_rows",
             "historical_game_to_game_volatility","usage_share_volatility","opponent_history_mean","recent_form_delta",
             "implied_team_total","team_spread","game_total","projected_team_points","projected_opponent_points")
    categorical=("team","opponent","archetype","home_away","favorite_status")
    for market in sorted({str(row["market"]) for row in rows}):
        market_rows=[row for row in _one_per_base(rows) if row["market"]==market]
        for test_week in weeks:
            prior=sorted({int(row["week"]) for row in market_rows if int(row["week"])<test_week})
            train=[row for row in market_rows if int(row["week"])<test_week]; test=[row for row in market_rows if int(row["week"])==test_week]
            if len(prior)<2 or len(train)<min_train_rows or len(test)<min_test_rows: continue
            columns=[name for name in (*numeric,*categorical) if any(row.get(name) is not None for row in train)]
            numeric_used=[name for name in numeric if name in columns]; categorical_used=[name for name in categorical if name in columns]
            pre=ColumnTransformer([("numeric",SimpleImputer(strategy="median"),numeric_used),
                ("categorical",Pipeline([("imputer",SimpleImputer(strategy="most_frequent")),
                                         ("onehot",OneHotEncoder(handle_unknown="ignore"))]),categorical_used)])
            model=Pipeline([("features",pre),("model",RandomForestRegressor(n_estimators=150,min_samples_leaf=8,random_state=seed,n_jobs=1))])
            xtrain=DataFrame([{name:row.get(name) for name in columns} for row in train]); xtest=DataFrame([{name:row.get(name) for name in columns} for row in test])
            ytrain=np.log(np.array([(float(row["actual_stat"])-float(row["simulated_mean"]))**2+.25 for row in train]))
            ytest=np.log(np.array([(float(row["actual_stat"])-float(row["simulated_mean"]))**2+.25 for row in test]))
            model.fit(xtrain,ytrain); predicted=np.maximum(.01,np.exp(model.predict(xtest))-.25)
            residual=np.array([float(row["actual_stat"])-float(row["simulated_mean"]) for row in test]); baseline=np.array([float(row["simulated_stddev"])**2 for row in test])
            folds.append({"market":market,"test_week":test_week,"train_weeks":prior,"train_rows":len(train),"test_rows":len(test),
                          "baseline_gaussian_nll":_gaussian_nll(residual,baseline),"learned_variance_gaussian_nll":_gaussian_nll(residual,predicted),
                          "nll_improvement":_gaussian_nll(residual,baseline)-_gaussian_nll(residual,predicted)})
            importance=permutation_importance(model,xtest,ytest,n_repeats=5,random_state=seed,scoring="neg_mean_squared_error")
            for name,value,std in zip(columns,importance.importances_mean,importance.importances_std):
                importances.append({"market":market,"test_week":test_week,"feature":name,"importance":float(value),"importance_std":float(std),"test_rows":len(test)})
    return ({"status":"COMPLETE" if folds else "INSUFFICIENT_HISTORY","reason":None if folds else "no market met walk-forward row thresholds",
             "weeks":weeks,"folds":folds,"average_nll_improvement":sum(row["nll_improvement"] for row in folds)/len(folds) if folds else None},
            sorted(importances,key=lambda row:(row["market"],row["test_week"],-row["importance"],row["feature"])))


def _calibration_metrics(probabilities: list[float], outcomes: list[int]) -> dict[str,float]:
    ps=np.clip(np.asarray(probabilities,dtype=float),1e-12,1-1e-12); ys=np.asarray(outcomes,dtype=float)
    bins=np.minimum(9,(ps*10).astype(int)); ece=sum(np.sum(bins==index)/len(ps)*abs(float(np.mean(ps[bins==index]))-float(np.mean(ys[bins==index]))) for index in sorted(set(bins)))
    return {"brier":float(np.mean((ps-ys)**2)),"log_loss":float(np.mean(-(ys*np.log(ps)+(1-ys)*np.log(1-ps)))),"ece":float(ece)}


def walk_forward_calibration(rows: list[dict[str,Any]], seed: int, min_train_rows: int,
                             min_test_rows: int) -> dict[str,Any]:
    weeks=sorted({int(row["week"]) for row in rows}); folds=[]; base=_one_per_base(rows)
    if len(weeks)<3: return {"status":"INSUFFICIENT_HISTORY","reason":"requires at least three evaluated weeks so every test fold has two prior weeks","weeks":weeks,"folds":[]}
    from scipy.stats import spearmanr
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression
    for market in sorted({str(row["market"]) for row in base}):
        values=[row for row in base if row["market"]==market and row["grade"] in {"WIN","LOSS"}]
        for test_week in weeks:
            prior=sorted({int(row["week"]) for row in values if int(row["week"])<test_week})
            train=[row for row in values if int(row["week"])<test_week]; test=[row for row in values if int(row["week"])==test_week]
            if len(prior)<2 or len(train)<min_train_rows or len(test)<min_test_rows: continue
            ptrain=np.clip(np.array([float(row["model_probability"]) for row in train]),1e-6,1-1e-6); ytrain=np.array([row["grade"]=="WIN" for row in train],dtype=int)
            ptest=np.clip(np.array([float(row["model_probability"]) for row in test]),1e-6,1-1e-6); ytest=np.array([row["grade"]=="WIN" for row in test],dtype=int)
            if len(set(ytrain))<2: continue
            isotonic=IsotonicRegression(out_of_bounds="clip").fit(ptrain,ytrain); piso=np.clip(isotonic.predict(ptest),1e-6,1-1e-6)
            xtrain=np.column_stack((np.log(ptrain),np.log1p(-ptrain))); xtest=np.column_stack((np.log(ptest),np.log1p(-ptest)))
            beta=LogisticRegression(random_state=seed,C=1e6,max_iter=2000).fit(xtrain,ytrain); pbeta=np.clip(beta.predict_proba(xtest)[:,1],1e-6,1-1e-6)
            raw=_calibration_metrics(ptest.tolist(),ytest.tolist())
            for method,predicted in (("isotonic",piso),("beta",pbeta)):
                calibrated=_calibration_metrics(predicted.tolist(),ytest.tolist())
                rank=float(spearmanr(ptest,predicted).statistic)
                folds.append({"market":market,"method":method,"test_week":test_week,"train_weeks":prior,"train_rows":len(train),"test_rows":len(test),
                              "raw":raw,"calibrated":calibrated,"brier_improvement":raw["brier"]-calibrated["brier"],
                              "ece_improvement":raw["ece"]-calibrated["ece"],"rank_correlation":rank if math.isfinite(rank) else None})
    return {"status":"COMPLETE" if folds else "INSUFFICIENT_HISTORY","reason":None if folds else "no market met walk-forward row thresholds","weeks":weeks,"folds":folds}


def research(*, season: int, start_week: int, end_week: int, snapshot_root: Path,
             season_results_dir: Path, output_dir: Path, seed: int=1729,
             min_train_rows: int=100, min_test_rows: int=20,
             min_segment_size: int=20) -> dict[str,Any]:
    rows,exclusions,input_paths=load_joined_analysis_rows(season=season,start_week=start_week,end_week=end_week,
        snapshot_root=snapshot_root,season_results_dir=season_results_dir)
    rows,context_paths=enrich_context(rows,snapshot_root,season,start_week,end_week); input_paths += context_paths
    distributions=compare_distributions(rows); availability=feature_availability(rows); clusters=residual_clusters(rows,min_segment_size)
    variance,importance=walk_forward_variance(rows,seed,min_train_rows,min_test_rows)
    calibration=walk_forward_calibration(rows,seed,min_train_rows,min_test_rows)
    recommendations=[]
    for market in sorted({row["market"] for row in distributions}):
        best=sorted((row for row in distributions if row["market"]==market),
                    key=lambda row:(row["composite_rank"],row["brier"],row["average_negative_log_likelihood"],row["family"]))[0]
        recommendations.append({"market":market,"recommended_family":best["family"],"composite_rank":best["composite_rank"],
                                "brier":best["brier"],"average_negative_log_likelihood":best["average_negative_log_likelihood"],
                                "selection_policy":"lowest NLL+Brier rank sum; ties use Brier then NLL",
                                "evidence_weeks":sorted({int(row["week"]) for row in rows if row["market"]==market}),
                                "evidence_status":"MULTIWEEK" if len({int(row["week"]) for row in rows if row["market"]==market})>=2 else "SINGLE_WEEK_PRELIMINARY"})
    ranked_clusters=sorted((row for row in clusters if row["eligible_for_ranking"] and row["model_minus_market_brier"] is not None),
                           key=lambda row:(-row["model_minus_market_brier"],row["dimension"],row["value"]))
    summary={"schema_version":1,"season":season,"weeks":[start_week,end_week],"network_contacted":False,
             "joined_rows":len(rows),"excluded_rows":len(exclusions),"exclusions_by_reason":dict(sorted(Counter(row["reason"] for row in exclusions).items())),
             "evaluated_weeks":sorted({int(row["week"]) for row in rows}),"distribution_recommendations":recommendations,
             "variance_model_status":variance["status"],"calibration_status":calibration["status"],
             "permutation_importance_status":"COMPLETE" if importance else "INSUFFICIENT_HISTORY",
             "top_residual_error_clusters":ranked_clusters[:25],
             "guardrails":["Distribution comparisons reuse frozen pregame means and variances; no outcome is used to generate a forecast.",
                           "Variance and calibration models train only on earlier weeks and never fit with fewer than two prior weeks.",
                           "Recommendations are research evidence and do not mutate production probabilities."]}
    artifacts={"research_summary.json":summary,"distribution_comparison.json":distributions,
               "variance_model_report.json":variance,"calibration_report.json":calibration,
               "permutation_importance.json":importance,"residual_clusters.json":clusters,
               "feature_availability.json":availability}
    output_dir.mkdir(parents=True,exist_ok=True)
    for name,value in artifacts.items(): _write_json(output_dir/name,value)
    _write_csv(output_dir/"distribution_comparison.csv",distributions); _write_csv(output_dir/"permutation_importance.csv",importance); _write_csv(output_dir/"residual_clusters.csv",clusters)
    manifest={"schema_version":1,"network_contacted":False,
              "config":{"season":season,"start_week":start_week,"end_week":end_week,"seed":seed,"min_train_rows":min_train_rows,"min_test_rows":min_test_rows,"min_segment_size":min_segment_size},
              "inputs":{path.as_posix():_semantic_hash(path) for path in sorted(set(input_paths))},
              "artifacts":{name:_hash(output_dir/name) for name in sorted((*JSON_ARTIFACTS,*CSV_ARTIFACTS))}}
    _write_json(output_dir/"research_manifest.json",manifest)
    return {**artifacts,"research_manifest.json":manifest}


def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--season",type=int,required=True)
    parser.add_argument("--start-week",type=int,default=1); parser.add_argument("--end-week",type=int,default=18)
    parser.add_argument("--snapshot-root",type=Path,default=SNAPSHOTS_DIR); parser.add_argument("--season-results-dir",type=Path,required=True); parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--seed",type=int,default=1729); parser.add_argument("--min-train-rows",type=int,default=100); parser.add_argument("--min-test-rows",type=int,default=20); parser.add_argument("--min-segment-size",type=int,default=20)
    research(**vars(parser.parse_args(argv))); return 0


if __name__=="__main__": raise SystemExit(main())
