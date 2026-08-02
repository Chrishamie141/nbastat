import json

import pytest

from backtesting.model_registry import (
    best_distribution, promotion_check, rebuild_index, register_experiment, register_model,
    validate_registry,
)


def _model(model_id="nfl_game_baseline_v3"):
    return {"schema_version":1,"model_id":model_id,"sport":"nfl","target":"player_props",
            "state":"benchmark","git_commit":"abc123","feature_set":{"version":"v3","features":["history"]},
            "distribution":{"family":"empirical_simulation"},"variance":{"method":"simulation"},
            "calibration":{"method":"none"}}


def _experiment(model_id="nfl_game_baseline_v3", experiment_id="baseline.exp.2025"):
    return {"schema_version":1,"experiment_id":experiment_id,"model_id":model_id,"git_commit":"abc123",
            "configuration_hash":"f"*64,"training_window":{"strategy":"walk_forward","weeks":list(range(1,15))},
            "evaluation_window":{"season":2025,"requested_weeks":list(range(1,19)),"evaluated_weeks":list(range(1,16))},
            "dataset":{"opportunities":2000,"independent_games":120,"input_hashes":{}},
            "metrics":{"overall":{"brier_score":.2,"log_loss":.6,"ece":.04,"roi":.03},
                       "by_market":{},"by_confidence_bucket":{},"calibration_curve":[]},
            "reproducibility":{"seed":1729,"simulations":10000,"network_contacted":False,"deterministic":True},
            "evidence":{"leakage_safe":True,"out_of_sample":True,"status":"PROMOTION_ELIGIBLE_SAMPLE"},
            "baseline_comparison":{"baseline_model_id":"nfl_game_baseline_v3","paired_opportunities":True,
                "metric_deltas":{"brier_score":{"estimate":-.02,"ci_95":[-.03,-.01]},
                    "log_loss":{"estimate":-.04,"ci_95":[-.06,-.01]},"roi":{"estimate":.03,"ci_95":[.01,.05]},
                    "ece":{"estimate":-.01,"ci_95":[-.02,0]}}},
            "artifacts":{"summary.json":{"path":"summary.json","sha256":"a"*64}}}


def test_registry_is_content_addressed_append_only_and_validates(tmp_path):
    root=tmp_path/"registry"; model=_model(); experiment=_experiment()
    register_model(root,model); register_experiment(root,experiment)
    assert validate_registry(root) == {"status":"VALID","models":1,"experiments":1,"promotions":0}
    register_model(root,model)  # identical registration is idempotent
    changed={**model,"distribution":{"family":"normal"}}
    with pytest.raises(FileExistsError,match="immutable"):
        register_model(root,changed)
    index=json.loads((root/"index.json").read_text())
    index["models"][model["model_id"]]["sha256"]="0"*64
    (root/"index.json").write_text(json.dumps(index))
    with pytest.raises(ValueError,match="stale"):
        validate_registry(root)


def test_promotion_policy_fails_closed_and_requires_uncertainty():
    qualified=_experiment(model_id="nfl_prop_nb_v1",experiment_id="nb.exp.2025")
    assert promotion_check(qualified,"nfl_game_baseline_v3")["eligible"] is True
    one_week={**qualified,"evaluation_window":{**qualified["evaluation_window"],"evaluated_weeks":[1]},
              "baseline_comparison":None}
    decision=promotion_check(one_week,"nfl_game_baseline_v3")
    assert decision["eligible"] is False
    assert "fewer than 15 evaluated weeks" in decision["reasons"]
    assert "missing comparison against the registered baseline" in decision["reasons"]


def test_experiment_must_reference_registered_model(tmp_path):
    root=tmp_path/"registry"; rebuild_index(root)
    with pytest.raises(FileNotFoundError,match="registered first"):
        register_experiment(root,_experiment())


def test_distribution_resolution_fails_closed_for_experimental_model(tmp_path):
    root=tmp_path/"registry"; model=_model("nfl_prop_v4_research_v1")
    model={**model,"state":"experimental","distribution":{"family":"market_specific","backends":{"receiving_yards":"normal"}}}
    register_model(root,model)
    with pytest.raises(PermissionError,match="allow_experimental"):
        best_distribution(root,"receiving_yards",model_id=model["model_id"])
    resolved=best_distribution(root,"receiving_yards",model_id=model["model_id"],allow_experimental=True)
    assert resolved["family"] == "normal" and resolved["experimental"] is True
