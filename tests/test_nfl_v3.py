from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from backtesting.nfl_game_predictor import NFLGameMarketPredictor
from backtesting.nfl_v3 import (FeatureSnapshot, FeatureValue, NFLGameMarketPredictorV3,
    NFLResearchSplit, NFLV3Config, ProbabilityCalibrator, V3_MODEL_VERSION,
    chronological_folds, create_holdout_manifest, verify_holdout_manifest)


def row(team, opponent, week, points, allowed):
    stamp=f"2024-10-{week:02d}T20:00:00Z"
    return {"team":team,"opponent":opponent,"week":week,"season":2024,"game_id":f"{week}-{team}",
      "points_for":points,"points_against":allowed,"completed_at":stamp,"data_as_of":stamp,
      "record_role":"completed_game_history","is_pregame":False,"home_away":"home"}

GAME={"game_id":"target","week":1,"season":2025,"home_team":"BUF","away_team":"MIA","kickoff_time":"2025-09-07T17:00:00Z"}
HISTORY=[r for w in range(1,7) for r in (row("BUF","MIA",w,20+w,18),row("MIA","BUF",w,18,20+w))]


def test_split_rejects_holdout_tuning_and_folds_are_chronological():
    split=NFLResearchSplit()
    assert split.window(6)=="development" and split.window(7)=="holdout"
    with pytest.raises(ValueError,match="holdout outcomes"): split.assert_tuning_weeks([2,7])
    assert chronological_folds([3,1,2])==[([1],[2]),([1,2],[3])]


def test_feature_timestamp_and_provenance_are_enforced():
    snap=FeatureSnapshot(GAME["kickoff_time"])
    snap.add("offense","points",FeatureValue(24,("2025-09-06T00:00:00Z",),"history"))
    assert snap.diagnostics()["offense"]["points"]["provenance"]=="history"
    with pytest.raises(ValueError,match="strictly before"): snap.add("bad","future",FeatureValue(1,(GAME["kickoff_time"],),"outcome"))


def test_v3_separates_market_and_football_and_exposes_fallbacks():
    predictor=NFLGameMarketPredictor(V3_MODEL_VERSION,NFLV3Config(market_blend_weight=.2))
    p=predictor.project(GAME,HISTORY,{"moneyline_probability":.8,"captured_at":"2025-09-07T16:00:00Z"})
    assert p.model_version==V3_MODEL_VERSION
    assert p.features["football_probability"] != .8
    assert p.features["blended_probability"] == pytest.approx(.8*p.features["football_probability"]+.16)
    assert predictor.last_feature_diagnostics["injuries"]["available"]["feature_missing"]
    future=row("BUF","MIA",8,99,0); future["data_as_of"]=future["completed_at"]="2025-09-08T20:00:00Z"
    assert predictor.project(GAME,HISTORY+[future]).home_points==p.home_points


def test_score_distribution_config_hash_and_ablation_are_deterministic():
    config=NFLV3Config(); predictor=NFLGameMarketPredictorV3(config); p=predictor.project(GAME,HISTORY)
    assert p.expected_margin==pytest.approx(p.home_points-p.away_points)
    assert p.expected_total==pytest.approx(p.home_points+p.away_points)
    assert 0 < p.probability("h2h","BUF",home_team="BUF",away_team="MIA") < 1
    assert config.configuration_hash==NFLV3Config().configuration_hash
    assert replace(config,disabled_feature_groups=("market_context",)).configuration_hash != config.configuration_hash


def test_calibration_is_development_only_and_walk_forward_safe():
    split=NFLResearchSplit(); cal=ProbabilityCalibrator("platt").fit([(.6,1,1),(.4,0,2)],split)
    assert cal.sample_size==2 and 0<cal.predict(.7)<1
    with pytest.raises(ValueError): ProbabilityCalibrator().fit([(.5,1,7)],split)


def test_holdout_manifest_freezes_config_and_snapshots(tmp_path: Path):
    path=tmp_path/"manifest.json"; config=NFLV3Config(); hashes={"week_7/games.json":"abc"}
    create_holdout_manifest(path,2025,NFLResearchSplit(),config,hashes)
    assert verify_holdout_manifest(path,config,hashes)["season"]==2025
    with pytest.raises(ValueError,match="configuration"): verify_holdout_manifest(path,replace(config,market_blend_weight=.2),hashes)
    with pytest.raises(ValueError,match="snapshot"): verify_holdout_manifest(path,config,{"week_7/games.json":"changed"})


def test_v1_v2_constants_remain_and_v3_uses_projection_contract():
    assert NFLGameMarketPredictor("nfl_game_baseline_v1").HOME_FIELD_POINTS==1.5
    assert NFLGameMarketPredictor("nfl_game_baseline_v2").config.decay==.9
    p=NFLGameMarketPredictor(V3_MODEL_VERSION).project(GAME,HISTORY)
    payload=p.output(home_team="BUF",away_team="MIA",spread=-3,total=45)
    assert {"home_cover_probability","over_probability","model_version"} <= payload.keys()
