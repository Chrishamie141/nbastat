from __future__ import annotations

from backtesting.player_history import (canonicalize_player_history, extract_player_outcome,
    filter_player_history, history_known_at, normalize_position, player_game_identity)
from backtesting.nfl_simulation import grade_player_prop


def game(gid="g1", kickoff="2025-09-07T17:00:00Z"):
    return {"game_id":gid,"season":2025,"week":1,"home_team":"BUF","away_team":"MIA","kickoff_time":kickoff}


def test_real_shaped_espn_long_to_wide_zero_identity_and_known_at():
    rows=[
      {"game_id":"g1","player_id":"42","player":"A Player","team":"BUF","position":"QB","season":"2025","week":1,
       "source":"espn","stats":{"completions":0,"attempts":2,"passing_yards":0,"passing_touchdowns":0}},
      {"game_id":"g1","player_id":"42","player":"A Player","team":"BUF","position":"QB","season":"2025","week":1,
       "source":"espn","stats":{"attempts":3,"rushing_yards":11}},
    ]
    out,audit=canonicalize_player_history(rows,games={"g1":game()})
    assert len(out)==1 and audit["provider_rows"]==2
    assert out[0]["passing_attempts"]==2 and out[0]["rushing_attempts"]==3
    assert out[0]["passing_yards"]==0 and out[0]["passing_tds"]==0
    assert out[0]["player_id"]=="42" and out[0]["known_at"]=="2025-09-07T23:00:00Z"
    assert history_known_at(out[0]).isoformat()=="2025-09-07T23:00:00+00:00"


def test_duplicates_are_safe_conflicts_are_reported_and_field_rejected():
    base={"game_id":"g1","player":"Runner","team":"BUF","stats":{"rushing_yards":10}}
    out,audit=canonicalize_player_history([base,base,{**base,"stats":{"rushing_yards":12}}],games={"g1":game()})
    assert "rushing_yards" not in out[0]
    assert audit["conflicts"]==[{"identity":("g1","BUF","name:runner"),"field":"rushing_yards","values":[10,12]}]


def test_temporal_filter_target_future_prior_season_team_change_and_histogram():
    def row(gid,known,team="BUF",season=2025):
        return {"game_id":gid,"player_id":"7","player_name":"P","team":team,"season":season,
          "record_role":"completed_game_history","known_at":known,"passing_yards":1}
    target={**game("target","2025-09-14T17:00:00Z"),"prediction_cutoff":"2025-09-13T17:00:00Z"}
    rows=[row("old","2025-09-01T00:00:00Z"),row("prior","2024-12-01T00:00:00Z",season=2024),
          row("target","2025-09-14T23:00:00Z"),row("future","2025-09-13T18:00:00Z"),
          row("old-team","2025-08-01T00:00:00Z",team="NYJ")]
    result=filter_player_history(target,rows)
    assert [r["game_id"] for r in result.rows]==["old","prior"]
    assert result.rejection_histogram["future_timestamp"]==2
    assert result.rejection_histogram["irrelevant_team"]==1


def test_position_fallback_missing_fields_collision_outcome_and_grading():
    assert normalize_position("HB")=="RB" and normalize_position(None)=="UNKNOWN"
    rows=[{"game_id":"g1","player_id":"x","player":"One","team":"BUF","stats":{"receptions":3}},
          {"game_id":"g2","player_id":"x","player":"Two","team":"BUF","stats":{"passing_yards":250}}]
    out,audit=canonicalize_player_history(rows,games={"g1":game(),"g2":game("g2")})
    assert {r["position"] for r in out}=={"WR","QB"} and "x" in audit["identity_collisions"]
    assert player_game_identity(out[0])[2]=="x"
    actual=extract_player_outcome(out,"g2","x")
    assert actual and grade_player_prop("passing_yards",250,"under",actual)=="push"
    assert grade_player_prop("passing_yards",249.5,"over",actual)=="win"


def test_missing_identity_team_stats_are_machine_readable():
    rows=[{"game_id":"g","team":"BUF","stats":{"receptions":1}},
          {"game_id":"g","player":"P","stats":{"receptions":1}},
          {"game_id":"g","player":"P","team":"BUF","stats":{}}]
    out,audit=canonicalize_player_history(rows)
    assert out==[]
    assert audit["rejections"]["missing_player_identity"]==1
    assert audit["rejections"]["missing_team_identity"]==1
    assert audit["rejections"]["missing_stat_values"]==1
