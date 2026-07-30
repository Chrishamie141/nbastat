from __future__ import annotations

import numpy as np
import pytest

from backtesting.nfl_simulation import (GameTotal, Moneyline, NFLGameSimulator,
    PlayerPassingYards, Spread, grade_player_prop)


def fixture():
    game={"game_id":"game","home_team":"BUF","away_team":"MIA","kickoff_time":"2025-09-10T00:00:00Z",
          "projected_home_points":27,"projected_away_points":21}
    rows=[]
    for team, players in (("BUF",(("q1","Josh Allen","QB"),("w1","Receiver","WR"),("r1","Runner","RB"))),
                          ("MIA",(("q2","Other QB","QB"),("w2","Other WR","WR"),("r2","Other RB","RB")))):
        for week in range(1,5):
            for pid,name,pos in players:
                rows.append({"game_id":f"old-{week}","team":team,"player_id":pid,"player_name":name,"position":pos,
                    "data_as_of":f"2025-09-0{week}T00:00:00Z","passing_yards":230 if pos=="QB" else 0,
                    "passing_tds":2 if pos=="QB" else 0,"rushing_attempts":16 if pos=="RB" else 1,
                    "rushing_yards":65 if pos=="RB" else 3,"receptions":5 if pos=="WR" else 1,
                    "receiving_yards":75 if pos=="WR" else 4})
    return game,rows


def test_rng_reproducibility_markets_players_and_joint_counting():
    game,rows=fixture(); sim=NFLGameSimulator()
    a=sim.simulate(game,[],rows,None,"nfl_game_baseline_v3",2000,141)
    b=sim.simulate(game,[],rows,None,"nfl_game_baseline_v3",2000,141)
    c=sim.simulate(game,[],rows,None,"nfl_game_baseline_v3",2000,142)
    assert np.array_equal(a.home_points,b.home_points)
    assert not np.array_equal(a.home_points,c.home_points)
    assert np.all(a.home_points>=0) and np.all(a.away_points>=0)
    assert sum(a.market_probability("moneyline").values()) == pytest.approx(1)
    assert sum(a.market_probability("spread",-3).values()) == pytest.approx(1)
    assert sum(a.market_probability("total",48).values()) == pytest.approx(1)
    joint=a.probability_of([Moneyline("BUF"),PlayerPassingYards("Josh Allen",over=200),GameTotal(over=45)])
    masks=Moneyline("BUF").mask(a)&PlayerPassingYards("Josh Allen",over=200).mask(a)&GameTotal(over=45).mask(a)
    assert joint.sample_hits == int(masks.sum())
    assert joint.joint_probability == pytest.approx(masks.mean())


def test_shared_script_correlations_and_pushes():
    game,rows=fixture(); result=NFLGameSimulator().simulate(game,[],rows,None,"v3",10000,9)
    assert result.correlation(("Josh Allen","passing_yards"),("Receiver","receiving_yards"))["pearson"] > 0
    assert np.corrcoef(result.score_margin,result.player_outcomes[("Runner","rushing_attempts")])[0,1] > 0
    manual=result.market_probability("total",int(result.game_total[0]))
    assert manual["push"] > 0


def test_grading_and_leakage_guard():
    assert grade_player_prop("passing_yards",250,"over",{"passing_yards":251}) == "win"
    assert grade_player_prop("passing_yards",250,"under",{"passing_yards":250}) == "push"
    game,rows=fixture(); rows[0]["data_as_of"]="2026-01-01T00:00:00Z"
    with pytest.raises(ValueError,match="future history"):
        NFLGameSimulator().simulate(game,[],rows,None,"v3",10,1)
