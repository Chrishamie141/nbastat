from __future__ import annotations

from backtesting.system_a.events import normalize_events
from backtesting.system_a.ledgers import build_ledgers
import random


def _play(play_id, kind, **values):
    return {"canonical_game_id": "g1", "provider_play_id": str(play_id), "provider_name": "fixture",
            "canonical_offense_team_id": "a", "canonical_defense_team_id": "b",
            "play_sequence": play_id, "quarterback_id": "qb", "event_type": kind, **values}


def test_all_launch_opportunity_partitions_conserve():
    raw = [
        _play(1, "COMPLETION", target_player_id="wr", completed_pass=True, receiving_yards=12),
        _play(2, "PASS", target_player_id="wr"), _play(3, "THROWAWAY"), _play(4, "SACK"),
        _play(5, "SCRAMBLE", rushing_yards=7), _play(6, "RUSH", rusher_id="rb", rushing_yards=4),
        _play(7, "KNEEL", rushing_yards=-1), _play(8, "TEAM_RUSH", rushing_yards=-3),
    ]
    events, findings = normalize_events(raw)
    assert findings == []
    result = build_ledgers(events)
    assert result["quarantine"] == []
    assert result["reconciliation"]["accepted_rows_unresolved_accounting_violations"] == 0
    assert result["team_game_dropback_ledger"][0]["partition_valid"] is True
    assert result["team_game_pass_attempt_allocation_ledger"][0]["allocation_valid"] is True
    assert result["team_game_rush_partition_ledger"][0]["partition_valid"] is True
    assert result["player_game_target_reception_ledger"] == [{
        "canonical_game_id": "g1", "canonical_player_id": "wr", "targets": 2,
        "receptions": 1, "receiving_yards": 12.0,
    }]


def test_receiver_target_identity_conflict_is_excluded():
    events, _ = normalize_events([_play(1, "COMPLETION", target_player_id="wr1", receiver_id="wr2",
                                         completed_pass=True, receiving_yards=8)])
    result = build_ledgers(events)
    assert result["accepted_events"] == []
    assert result["quarantine"][0]["reason_code"] == "RECEPTIONS_EXCEED_TARGETS"


def test_many_synthetic_valid_games_conserve_under_random_order():
    generator = random.Random(1729)
    for game_index in range(100):
        rows = []
        for play_id in range(1, generator.randint(8, 40)):
            kind = generator.choice(("PASS", "COMPLETION", "SPIKE", "THROWAWAY", "SACK", "SCRAMBLE", "RUSH"))
            values = {"target_player_id": f"wr{play_id % 4}"} if kind in {"PASS", "COMPLETION"} else {}
            if kind == "COMPLETION": values.update(completed_pass=True, receiving_yards=generator.randint(-5, 70))
            if kind == "RUSH": values.update(rusher_id=f"rb{play_id % 3}", rushing_yards=generator.randint(-8, 50))
            row = _play(play_id, kind, **values); row["canonical_game_id"] = f"g{game_index}"
            rows.append(row)
        generator.shuffle(rows)
        events, findings = normalize_events(rows)
        assert findings == []
        result = build_ledgers(events)
        assert result["quarantine"] == []
        assert all(row["partition_valid"] for row in result["team_game_dropback_ledger"])
        assert all(row["allocation_valid"] for row in result["team_game_pass_attempt_allocation_ledger"])
        assert all(row["partition_valid"] for row in result["team_game_rush_partition_ledger"])
