from dataclasses import replace
import json

import pytest

from backtesting.evaluate_nfl_bet_engine import render_markdown, write_artifacts
from backtesting.nfl_bet_engine import (
    DEFAULT_POLICIES, BetCandidate, ConservativeJointProbabilityEstimator,
    RiskProfile, TicketEngine, TicketType, _make_ticket, american_to_decimal,
    aggregate_rejections, conflict_reason, grade_ticket, normalize_candidate,
    parlay_reliability, rank_winner_anchors, render_recommendation_slate,
)


def candidate(game="g1", market="h2h", selection="A", probability=.7, implied=.55,
              odds=120, line=None, home="A", away="B", model="nfl_game_baseline_v1"):
    return normalize_candidate({"league":"nfl","season":"2025","week":1,"game_id":game,
        "kickoff_time":"2025-09-01T20:00:00Z","home_team":home,"away_team":away,
        "model_name":model,"market":market,"selection":selection,"line":line,
        "american_odds":odds,"implied_probability":implied,"model_probability":probability,
        "edge":probability-implied,"expected_value":probability*american_to_decimal(odds)-1,
        "bookmaker":"book","snapshot_timestamp":"2025-09-01T10:00:00Z","data_as_of":"2025-08-31T00:00:00Z"})


def test_normalization_distinguishes_confidence_from_value():
    favorite=candidate(probability=.8,implied=.78,odds=-355)
    dog=candidate(game="g2",probability=.48,implied=.30,odds=233,home="C",away="D",selection="C")
    assert favorite.confidence_score > dog.confidence_score
    assert dog.value_score > favorite.value_score
    assert favorite.edge == pytest.approx(.02)


def test_policy_profiles_are_explicit_and_ordered():
    assert DEFAULT_POLICIES[RiskProfile.SAFE].minimum_model_probability > DEFAULT_POLICIES[RiskProfile.AGGRESSIVE].minimum_model_probability
    assert DEFAULT_POLICIES[RiskProfile.SAFE].maximum_legs < DEFAULT_POLICIES[RiskProfile.AGGRESSIVE].maximum_legs


def test_conflict_and_duplicate_detection():
    a=candidate(); assert conflict_reason(a,a)=="duplicate_leg"
    assert conflict_reason(a,candidate(selection="B"))=="conflicting_selection"
    over=candidate(market="total",selection="over",line=45)
    assert conflict_reason(over,candidate(market="total",selection="under",line=45))=="conflicting_selection"


def test_independent_joint_probability_and_odds():
    legs=[candidate(),candidate(game="g2",home="C",away="D",selection="C")]
    probability,status=ConservativeJointProbabilityEstimator().estimate(legs)
    assert probability==pytest.approx(.49); assert status=="independence_assumption"
    ticket=_make_ticket(TicketType.SLATE_PARLAY,RiskProfile.BALANCED,legs,10,ConservativeJointProbabilityEstimator(),[])
    assert ticket.combined_decimal_odds==pytest.approx(4.84)


def test_sgp_never_multiplies_correlated_probability():
    legs=[candidate(),candidate(market="total",selection="over",line=45)]
    probability,status=ConservativeJointProbabilityEstimator().estimate(legs)
    assert probability is None and status=="correlated_probability_unavailable"


def permissive(profile=RiskProfile.BALANCED, **changes):
    base=DEFAULT_POLICIES[profile]
    return replace(base,minimum_model_probability=0,minimum_edge=-1,minimum_ev=-1,
                   minimum_confidence_score=0,minimum_value_score=0,**changes)


def test_winner_and_slate_diversification_and_no_bet():
    engine=TicketEngine({RiskProfile.BALANCED:permissive(minimum_legs=2,maximum_legs=3,maximum_legs_per_game=1)})
    same=[candidate(),candidate(market="spread",selection="A",line=-3)]
    assert engine.slate_parlay(same,RiskProfile.BALANCED).no_bet
    result=engine.winner_parlay([candidate(),candidate(game="g2",home="C",away="D",selection="C")],RiskProfile.BALANCED)
    assert len(result.tickets[0].legs)==2


def test_same_team_limit_reason_is_recorded():
    policy=permissive(minimum_legs=2,maximum_legs=3,maximum_team_exposure=1)
    engine=TicketEngine({RiskProfile.BALANCED:policy})
    result=engine.slate_parlay([candidate(),candidate(game="g2",home="A",away="C",selection="A")],RiskProfile.BALANCED)
    assert "same_team_exposure_limit" in {r.reason for r in result.rejections}


def test_sgp_coherent_script_and_unavailable_ev():
    engine=TicketEngine({RiskProfile.BALANCED:permissive()})
    result=engine.same_game_parlays([candidate(implied=.7,odds=-200),candidate(market="total",selection="over",line=45)],RiskProfile.BALANCED)
    assert result.tickets[0].estimated_joint_probability is None
    assert result.tickets[0].estimated_ticket_ev is None


def test_ticket_grading_push_void_and_loss():
    spread=candidate(market="spread",selection="A",line=-3,odds=-110)
    winner=candidate(game="g2",home="C",away="D",selection="C",odds=-110)
    ticket=_make_ticket(TicketType.SLATE_PARLAY,RiskProfile.BALANCED,[spread,winner],10,ConservativeJointProbabilityEstimator(),[])
    outcomes={"g1":{"home_team":"A","away_team":"B","final_home_score":20,"final_away_score":17},
              "g2":{"home_team":"C","away_team":"D","final_home_score":24,"final_away_score":10}}
    grade_ticket(ticket,outcomes)
    assert ticket.historical_grade=="win" and ticket.payout==pytest.approx(10*winner.decimal_odds)


def test_deterministic_generation_and_model_isolation():
    policy=permissive(minimum_legs=2,maximum_legs=2)
    engine=TicketEngine({RiskProfile.BALANCED:policy}); legs=[candidate(),candidate(game="g2",home="C",away="D",selection="C")]
    one=engine.winner_parlay(reversed(legs),RiskProfile.BALANCED).tickets[0]
    two=engine.winner_parlay(legs,RiskProfile.BALANCED).tickets[0]
    assert one.ticket_id==two.ticket_id
    with pytest.raises(ValueError,match="mixed_model_ticket"):
        _make_ticket(TicketType.SLATE_PARLAY,RiskProfile.BALANCED,[legs[0],replace(legs[1],model_name="nfl_game_baseline_v2")],10,ConservativeJointProbabilityEstimator(),[])


def test_json_csv_markdown_artifacts(tmp_path):
    result={"dataset":{"season":"2025","start_week":1,"end_week":1},"strategy_metrics":{},"weekly_performance":{},"rejections":[],"tickets":[]}
    paths=[tmp_path/"out.json",tmp_path/"tickets.csv",tmp_path/"report.md"]
    write_artifacts(result,*paths)
    assert json.loads(paths[0].read_text())["dataset"]["season"]=="2025"
    assert paths[1].read_text().startswith("ticket_id")
    assert "# NFL Betting Engine Evaluation" in paths[2].read_text()


def test_anchor_ranking_and_safe_underdog_separation():
    favorite=candidate(probability=.72,implied=.68,odds=-210)
    dog=candidate(game="g2",home="C",away="D",selection="C",probability=.44,implied=.35,odds=186)
    ranked=rank_winner_anchors([dog,favorite])
    assert ranked[0].candidate_id==favorite.candidate_id and ranked[0].anchor_rank==1
    engine=TicketEngine({RiskProfile.SAFE:permissive(RiskProfile.SAFE,minimum_legs=2)})
    assert engine.winner_parlay([favorite,dog],RiskProfile.SAFE).no_bet


def test_probability_status_extreme_ev_and_correlated_structural_score():
    longshot=candidate(probability=.9,implied=.1,odds=900)
    other=candidate(game="g2",home="C",away="D",selection="C",probability=.9,implied=.1,odds=900)
    ticket=_make_ticket(TicketType.SLATE_PARLAY,RiskProfile.BALANCED,[longshot,other],10,ConservativeJointProbabilityEstimator(),[])
    assert ticket.raw_joint_probability==pytest.approx(.81)
    assert ticket.adjusted_joint_probability is None and ticket.ticket_ev_status=="provisional"
    assert "extreme_estimated_ev" in ticket.warning_reasons
    sgp=TicketEngine({RiskProfile.BALANCED:permissive()}).same_game_parlays([candidate(implied=.7,odds=-200),candidate(market="total",selection="over",line=45)],RiskProfile.BALANCED).tickets[0]
    assert sgp.joint_probability_status=="unavailable_correlated"
    assert sgp.recommendation_score and sgp.raw_joint_probability is None


def test_recommendation_slate_renderer_aggregation_and_diagnostics():
    legs=[candidate(),candidate(game="g2",home="C",away="D",selection="C")]
    engine=TicketEngine({p:permissive(p,minimum_legs=2,maximum_legs=2) for p in RiskProfile})
    slate=engine.recommendation_slate(legs,"nfl_game_baseline_v1",1,top_n=1)
    assert len(slate.best_singles)==1 and set(slate.winner_parlays)=={"safe","balanced","aggressive"}
    assert "BEST SINGLES" in render_recommendation_slate(slate)
    summary=aggregate_rejections([{"reason":"x"},{"reason":"x"}],debug=False)
    assert summary["reason_counts"]=={"x":2} and "records" not in summary
    ticket=slate.winner_parlays["balanced"].ticket; ticket.historical_grade="win"
    diagnostics=parlay_reliability([ticket])
    assert diagnostics["leg_count_diagnostics"]["2"]["observed_hit_rate"]==1
