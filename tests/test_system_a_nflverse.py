from __future__ import annotations

import csv
import gzip
import json

from backtesting.system_a.events import normalize_events
from backtesting.system_a.ledgers import build_ledgers
from backtesting.system_a.nflverse import load_nflverse_events


def test_frozen_nflverse_adapter_uses_id_crosswalk_and_schedule(tmp_path):
    players = tmp_path / "players.csv"
    players.write_text("gsis_id,espn_id,position\nG-QB,E-QB,QB\nG-WR,E-WR,WR\nG-RB,E-RB,RB\n", encoding="utf-8")
    snapshots = tmp_path / "snapshots"
    week = snapshots / "nfl" / "2025" / "week_01"; week.mkdir(parents=True)
    (week / "games.json").write_text(json.dumps([{
        "game_id": "espn-1", "season": 2025, "week": 1, "away_team": "ARI", "home_team": "WAS",
    }]), encoding="utf-8")
    raw = tmp_path / "raw"; raw.mkdir()
    fields = ["season_type", "week", "game_id", "play_id", "play_type", "posteam", "defteam", "qtr", "time",
              "pass_attempt", "rush_attempt", "sack", "qb_scramble", "qb_kneel", "interception", "complete_pass",
              "passer_player_id", "receiver_player_id", "rusher_player_id", "receiving_yards", "rushing_yards",
              "two_point_attempt", "aborted_play", "lateral_reception", "lateral_rush", "yards_gained"]
    rows = [
        {"season_type": "REG", "week": 1, "game_id": "2025_01_ARI_WAS", "play_id": 1,
         "play_type": "pass", "posteam": "ARI", "defteam": "WAS", "qtr": 1, "time": "14:00",
         "pass_attempt": 1, "complete_pass": 1, "passer_player_id": "G-QB", "receiver_player_id": "G-WR",
         "receiving_yards": 12},
        {"season_type": "REG", "week": 1, "game_id": "2025_01_ARI_WAS", "play_id": 2,
         "play_type": "run", "posteam": "ARI", "defteam": "WAS", "qtr": 1, "time": "13:20",
         "rush_attempt": 1, "rusher_player_id": "G-RB", "rushing_yards": 5},
    ]
    with gzip.open(raw / "play_by_play_2025.csv.gz", "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    loaded = load_nflverse_events(raw_root=raw, players_path=players, snapshot_root=snapshots, seasons=(2025,))
    assert loaded["quarantine"] == []
    assert loaded["game_coverage"] == {(2025, 1): 1}
    assert loaded["records"][0]["target_player_id"] == "E-WR"
    events, findings = normalize_events(loaded["records"])
    assert findings == []
    ledgers = build_ledgers(events)
    assert ledgers["player_game_target_reception_ledger"][0]["receiving_yards"] == 12
    assert ledgers["player_game_rushing_ledger"][0]["canonical_player_id"] == "E-RB"
