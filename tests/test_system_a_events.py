from __future__ import annotations

import random

import pytest

from backtesting.system_a.events import normalize_event, normalize_events
from backtesting.system_a.temporal import TemporalSafetyError, validate_feature_cutoff


def _play(play_id: str, event_type: str, **values):
    return {
        "canonical_game_id": "g1", "provider_game_id": "pg1", "provider_play_id": play_id,
        "canonical_offense_team_id": "t1", "canonical_defense_team_id": "t2",
        "provider_name": "fixture", "play_sequence": int(play_id), "quarter": 1,
        "quarterback_id": "qb", "event_type": event_type, **values,
    }


@pytest.mark.parametrize(("kind", "expected"), [
    ("SPIKE", (1, 1, 0, 1)),
    ("THROWAWAY", (1, 1, 0, 1)),
    ("SACK", (1, 0, 0, 0)),
    ("SCRAMBLE", (1, 0, 1, 0)),
])
def test_provider_semantics_are_explicit(kind, expected):
    event, finding = normalize_event(_play("1", kind))
    assert finding is None
    assert (event.increments_dropback, event.increments_pass_attempt,
            event.increments_official_rush_attempt, event.non_target_attempt_count) == expected


def test_nullified_play_has_no_effects_and_overtime_is_explicit():
    event, finding = normalize_event(_play(
        "1", "COMPLETION", target_player_id="wr", completed_pass=True,
        receiving_yards=20, nullified_by_penalty=True, quarter=5,
    ))
    assert finding is None
    assert event.overtime is True
    assert sum((event.increments_dropback, event.increments_target, event.increments_reception)) == 0
    assert event.receiving_yards == 0


def test_unresolved_lateral_and_equal_version_duplicates_are_quarantined():
    _, finding = normalize_event(_play("1", "RUSH", rusher_id="rb", lateral_indicator=True))
    assert finding["reason_code"] == "LATERAL_RECONCILIATION_UNRESOLVED"
    events, findings = normalize_events([_play("2", "SPIKE"), _play("2", "SPIKE")])
    assert events == []
    assert {row["reason_code"] for row in findings} == {"DUPLICATE_PLAY"}


@pytest.mark.parametrize(("kind", "values", "family", "pass_result", "rush_category"), [
    ("PASS", {"target_player_id": "wr"}, "DROPBACK", "TARGETED_PASS", None),
    ("COMPLETION", {"target_player_id": "wr", "completed_pass": True}, "DROPBACK", "TARGETED_PASS", None),
    ("INTERCEPTION", {"target_player_id": "wr"}, "DROPBACK", "INTERCEPTION_WITH_TARGET", None),
    ("INTERCEPTION", {}, "DROPBACK", "INTERCEPTION_UNASSIGNED", None),
    ("BATTED_PASS", {}, "DROPBACK", "BATTED_OR_TIPPED_UNASSIGNED", None),
    ("QB_RUSH", {"rusher_id": "qb"}, "DESIGNED_RUSH", None, "DESIGNED_QB_RUSH"),
    ("RB_RUSH", {"rusher_id": "rb"}, "DESIGNED_RUSH", None, "DESIGNED_RB_WR_RUSH"),
    ("WR_RUSH", {"rusher_id": "wr"}, "DESIGNED_RUSH", None, "DESIGNED_RB_WR_RUSH"),
    ("KNEEL", {"rusher_id": "qb"}, "KNEEL", None, "QB_KNEEL"),
    ("TEAM_RUSH", {}, "TEAM_OR_ABORTED_RUSH", None, "TEAM_OR_ABORTED_RESIDUAL"),
    ("ABORTED_PLAY", {}, "TEAM_OR_ABORTED_RUSH", None, "TEAM_OR_ABORTED_RESIDUAL"),
])
def test_edge_case_classification(kind, values, family, pass_result, rush_category):
    event, finding = normalize_event(_play("1", kind, **values))
    assert finding is None
    assert (event.offensive_event_family, event.pass_attempt_result, event.rush_category) == (
        family, pass_result, rush_category,
    )


def test_no_play_missing_identities_and_correction_winner():
    no_play, finding = normalize_event(_play("1", "PASS", target_player_id="wr", no_play=True))
    assert finding is None and no_play.offensive_event_family == "NO_PLAY" and not no_play.counts_as_official_play
    missing_player, finding = normalize_event(_play("2", "RUSH"))
    assert missing_player is None and finding["reason_code"] == "UNRESOLVED_PLAYER_ID"
    missing_team = _play("3", "SPIKE"); missing_team.pop("canonical_defense_team_id")
    event, finding = normalize_event(missing_team)
    assert event is None and finding["reason_code"] == "UNRESOLVED_TEAM_ID"
    original = _play("4", "SPIKE", correction_version=1)
    corrected = _play("4", "THROWAWAY", correction_version=2, correction_status="CORRECTED")
    events, findings = normalize_events([corrected, original])
    assert events[0].pass_attempt_result == "THROWAWAY"
    assert findings[0]["disposition"] == "RETAINED_WARNING"


def test_normalization_is_deterministic_under_input_permutations():
    rows = [_play(str(i), "THROWAWAY") for i in range(1, 30)]
    expected, _ = normalize_events(rows)
    generator = random.Random(1729)
    for _ in range(20):
        shuffled = list(rows); generator.shuffle(shuffled)
        actual, _ = normalize_events(shuffled)
        assert actual == expected


def test_temporal_cutoff_is_strict_and_fails_closed():
    validate_feature_cutoff([{"information_time": "2025-09-01T10:00:00Z"}], "2025-09-01T11:00:00Z")
    with pytest.raises(TemporalSafetyError):
        validate_feature_cutoff([{"information_time": "2025-09-01T11:00:00Z"}], "2025-09-01T11:00:00Z")
    with pytest.raises(TemporalSafetyError):
        validate_feature_cutoff([{}], "2025-09-01T11:00:00Z")
