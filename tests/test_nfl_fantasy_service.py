from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nfl_fantasy_service import build_fantasy_rankings
from tools.build_nfl_runtime_stats import build


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    stats = {
        "source_season": 2025,
        "players": {
            "Target Receiver": {
                "position": "WR", "team": "BUF", "last_season_team": "BUF",
                "game_logs": [
                    {"week": 1, "REC_YDS": 100, "REC_TD": 1, "RECEPTIONS": 10},
                    {"week": 2, "REC_YDS": 80, "REC_TD": 0, "RECEPTIONS": 8},
                ],
            },
            "Other Receiver": {
                "position": "WR", "team": "MIA", "last_season_team": "MIA",
                "game_logs": [
                    {"week": 1, "REC_YDS": 90, "REC_TD": 1, "RECEPTIONS": 2},
                    {"week": 2, "REC_YDS": 90, "REC_TD": 0, "RECEPTIONS": 2},
                ],
            },
        },
    }
    context = {
        "as_of": "2026-08-04",
        "sources": [{"name": "Test consensus", "url": "https://example.test/ranks", "rankings": []}],
    }
    stats_path, context_path = tmp_path / "stats.json", tmp_path / "context.json"
    stats_path.write_text(json.dumps(stats), encoding="utf-8")
    context_path.write_text(json.dumps(context), encoding="utf-8")
    return stats_path, context_path


def test_fantasy_rankings_are_deterministic_and_scoring_sensitive(tmp_path):
    stats_path, context_path = _write_fixture(tmp_path)
    kwargs = {"stats_path": stats_path, "expert_context_path": context_path}

    ppr = build_fantasy_rankings("PPR", "WR", 25, **kwargs)
    repeated = build_fantasy_rankings("PPR", "WR", 25, **kwargs)
    standard = build_fantasy_rankings("STANDARD", "WR", 25, **kwargs)

    assert ppr == repeated
    assert ppr["items"][0]["player"] == "Target Receiver"
    assert ppr["items"][0]["lastSeasonFantasyPoints"] > standard["items"][0]["lastSeasonFantasyPoints"]
    assert ppr["sourceSeason"] == 2025
    assert ppr["researchAsOf"] == "2026-08-04"


def test_runtime_builder_merges_stat_categories_into_one_game(tmp_path):
    week = tmp_path / "snapshots" / "nfl" / "2025" / "week_01"
    week.mkdir(parents=True)
    rows = [
        {"player": "Test Player", "week": 1, "game_id": "g1", "team": "BUF", "stats": {"rushing_yards": 50, "rushing_tds": 1}},
        {"player": "Test Player", "week": 1, "game_id": "g1", "team": "BUF", "stats": {"receiving_yards": 20, "receiving_tds": 1, "receptions": 3}},
    ]
    (week / "player_stats.json").write_text(json.dumps(rows), encoding="utf-8")
    players_file = tmp_path / "players.csv"
    players_file.write_text("display_name,position_group,latest_team\nTest Player,RB,BUF\n", encoding="utf-8")

    report = build(tmp_path / "snapshots", tmp_path / "runtime.json", season=2025, players_file=players_file)
    player = report["players"]["Test Player"]

    assert player["games_played"] == 1
    assert player["game_logs"][0]["RUSH_TD"] == 1
    assert player["game_logs"][0]["REC_TD"] == 1
    assert player["season_stats"]["RECEPTIONS"] == 3


def test_fantasy_api_returns_real_draft_board(monkeypatch):
    import backend.app.main as api

    monkeypatch.setattr(api, "get_sports_mode", lambda: SimpleNamespace(phaseByLeague={"nfl": "preseason"}))
    response = api.nfl_fantasy_build({"scoring": "HALF_PPR", "position": "RB", "limit": 15}, user={"id": 7})

    assert response["action"] == "Fantasy Draft Builder"
    assert response["modelVersion"] == "nfl_fantasy_prior_season_v1"
    assert response["sourceSeason"] == 2025
    assert response["scoring"] == "HALF_PPR"
    assert response["items"]
    assert all(row["position"] == "RB" for row in response["items"])
    assert "placeholder" not in response["message"].lower()


@pytest.mark.parametrize("field,value", [("scoring", "BONUS"), ("position", "K")])
def test_fantasy_api_rejects_unsupported_options(field, value):
    import backend.app.main as api

    with pytest.raises(api.HTTPException) as exc:
        api._nfl_fantasy_response({field: value})
    assert exc.value.status_code == 400
