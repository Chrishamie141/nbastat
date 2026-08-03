"""Versioned canonical football event model and provider normalization."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable


SCHEMA_VERSION = 1
EVENT_FAMILIES = {"DROPBACK", "DESIGNED_RUSH", "KNEEL", "TEAM_OR_ABORTED_RUSH", "OTHER", "NO_PLAY"}
DROPBACK_RESULTS = {None, "PASS_ATTEMPT", "SACK", "SCRAMBLE"}
PASS_RESULTS = {None, "TARGETED_PASS", "SPIKE", "THROWAWAY", "BATTED_OR_TIPPED_UNASSIGNED",
                "OTHER_UNASSIGNED", "INTERCEPTION_WITH_TARGET", "INTERCEPTION_UNASSIGNED"}
RUSH_CATEGORIES = {None, "DESIGNED_RB_WR_RUSH", "DESIGNED_QB_RUSH", "QB_SCRAMBLE", "QB_KNEEL",
                   "TEAM_OR_ABORTED_RESIDUAL"}


@dataclass(frozen=True)
class CanonicalEvent:
    schema_version: int
    season: int
    week: int
    canonical_game_id: str
    provider_game_id: str
    play_id: str
    provider_play_id: str
    provider_name: str
    source_artifact_hash: str | None
    canonical_offense_team_id: str
    canonical_defense_team_id: str
    quarter: int
    clock: str | None
    play_sequence: int
    overtime: bool
    raw_record_reference: str
    counts_as_official_play: bool
    no_play: bool
    nullified_by_penalty: bool
    duplicate_status: str
    correction_status: str
    out_of_scope_reason: str | None
    offensive_event_family: str
    dropback_result: str | None
    pass_attempt_result: str | None
    rush_category: str | None
    quarterback_id: str | None
    target_player_id: str | None
    receiver_id: str | None
    rusher_id: str | None
    passer_id: str | None
    increments_dropback: int
    increments_pass_attempt: int
    increments_sack: int
    increments_target: int
    increments_completion: int
    increments_reception: int
    increments_official_rush_attempt: int
    receiving_yards: float
    rushing_yards: float
    sack_yards: float
    non_target_attempt_count: int
    team_rush_residual_count: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def quarantine(raw: dict[str, Any], code: str, explanation: str, *, affects: bool = True,
               retained: bool = False) -> dict[str, Any]:
    return {
        "provider": raw.get("provider_name"), "season": raw.get("season"), "week": raw.get("week"),
        "game_id": raw.get("canonical_game_id") or raw.get("game_id"),
        "play_id": raw.get("provider_play_id") or raw.get("play_id"),
        "canonical_player_id": raw.get("canonical_player_id"),
        "canonical_team_id": raw.get("canonical_offense_team_id") or raw.get("offense_team_id"),
        "raw_values": raw, "failed_rule": code, "reason_code": code,
        "explanation": explanation, "source_artifact_reference": raw.get("source_artifact_hash") or raw.get("source_reference"),
        "affects_launch_market_outputs": affects, "disposition": "RETAINED_WARNING" if retained else "EXCLUDED",
    }


def _required(raw: dict[str, Any], *names: str) -> bool:
    return all(raw.get(name) not in (None, "") for name in names)


def normalize_event(raw: dict[str, Any]) -> tuple[CanonicalEvent | None, dict[str, Any] | None]:
    """Normalize one already identity-resolved provider event.

    The repository currently stores no provider play-by-play; this adapter is
    exercised by provider fixtures and is ready for a later frozen export.
    """
    if not isinstance(raw, dict):
        return None, quarantine({}, "SOURCE_RECORD_MALFORMED", "event is not an object")
    if not _required(raw, "canonical_game_id", "provider_play_id", "canonical_offense_team_id",
                     "canonical_defense_team_id", "provider_name"):
        code = "UNRESOLVED_TEAM_ID" if not _required(raw, "canonical_offense_team_id", "canonical_defense_team_id") else "SOURCE_RECORD_MALFORMED"
        return None, quarantine(raw, code, "required event/game/team/provider identity is missing")
    if raw.get("lateral_indicator") and not raw.get("lateral_semantics_resolved"):
        return None, quarantine(raw, "LATERAL_RECONCILIATION_UNRESOLVED", "provider lateral allocation is unresolved")
    no_play = bool(raw.get("no_play"))
    nullified = bool(raw.get("nullified_by_penalty") or raw.get("penalty_nullified_play"))
    two_point = bool(raw.get("two_point_attempt"))
    event_type = str(raw.get("event_type") or "OTHER").upper()
    official = not (no_play or nullified or two_point)
    family = "NO_PLAY" if no_play or nullified else "OTHER"
    dropback_result = pass_result = rush_category = None
    dropback = pass_attempt = sack = target = completion = reception = rush = 0
    non_target = team_residual = 0
    quarterback = raw.get("quarterback_id")
    passer = raw.get("passer_id") or quarterback
    target_player = raw.get("target_player_id")
    receiver = raw.get("receiver_id")
    rusher = raw.get("rusher_id")
    receiving_yards = rushing_yards = sack_yards = 0.0
    out_of_scope = "TWO_POINT_ATTEMPT" if two_point else None

    if official and event_type == "SACK":
        family, dropback_result, dropback, sack = "DROPBACK", "SACK", 1, 1
        sack_yards = float(raw.get("sack_yards") or 0.0)
    elif official and event_type == "SCRAMBLE":
        if not quarterback:
            return None, quarantine(raw, "UNRESOLVED_PLAYER_ID", "scramble quarterback/rusher is unresolved")
        family, dropback_result, rush_category = "DROPBACK", "SCRAMBLE", "QB_SCRAMBLE"
        dropback = rush = 1; rusher = quarterback
        rushing_yards = float(raw.get("rushing_yards") or 0.0)
    elif official and event_type in {"PASS", "PASS_ATTEMPT", "COMPLETION", "INTERCEPTION", "SPIKE", "THROWAWAY", "BATTED_PASS"}:
        family, dropback_result, dropback, pass_attempt = "DROPBACK", "PASS_ATTEMPT", 1, 1
        interception = event_type == "INTERCEPTION" or bool(raw.get("interception"))
        if event_type == "SPIKE" or raw.get("spike"):
            pass_result, non_target = "SPIKE", 1
        elif event_type == "THROWAWAY" or raw.get("throwaway"):
            pass_result, non_target = "THROWAWAY", 1
        elif event_type == "BATTED_PASS" or raw.get("batted_or_tipped_unassigned"):
            pass_result, non_target = "BATTED_OR_TIPPED_UNASSIGNED", 1
        elif interception and target_player:
            pass_result, target = "INTERCEPTION_WITH_TARGET", 1
        elif interception:
            pass_result, non_target = "INTERCEPTION_UNASSIGNED", 1
        elif target_player:
            pass_result, target = "TARGETED_PASS", 1
            if raw.get("completed_pass") or event_type == "COMPLETION":
                receiver = receiver or target_player
                completion = reception = 1
                receiving_yards = float(raw.get("receiving_yards") or 0.0)
        else:
            pass_result, non_target = "OTHER_UNASSIGNED", 1
    elif official and event_type in {"RUSH", "DESIGNED_RUSH", "QB_RUSH", "WR_RUSH", "RB_RUSH"}:
        if not rusher:
            return None, quarantine(raw, "UNRESOLVED_PLAYER_ID", "designed rush has no canonical rusher")
        family, rush = "DESIGNED_RUSH", 1
        rush_category = "DESIGNED_QB_RUSH" if event_type == "QB_RUSH" or raw.get("designed_qb_rush") else "DESIGNED_RB_WR_RUSH"
        rushing_yards = float(raw.get("rushing_yards") or 0.0)
    elif official and event_type == "KNEEL":
        if not (rusher or quarterback):
            return None, quarantine(raw, "UNRESOLVED_PLAYER_ID", "kneel quarterback is unresolved")
        family, rush_category, rush = "KNEEL", "QB_KNEEL", 1
        rusher = rusher or quarterback
        rushing_yards = float(raw.get("rushing_yards") or 0.0)
    elif official and event_type in {"TEAM_RUSH", "ABORTED_RUSH", "ABORTED_PLAY"}:
        family, rush_category, rush, team_residual = "TEAM_OR_ABORTED_RUSH", "TEAM_OR_ABORTED_RESIDUAL", 1, 1
        rushing_yards = float(raw.get("rushing_yards") or 0.0)

    if not official:
        family = "NO_PLAY" if no_play or nullified else "OTHER"
        dropback = pass_attempt = sack = target = completion = reception = rush = non_target = team_residual = 0
        receiving_yards = rushing_yards = sack_yards = 0.0
    quarter = int(raw.get("quarter") or 0)
    event = CanonicalEvent(
        schema_version=SCHEMA_VERSION, season=int(raw.get("season") or 0), week=int(raw.get("week") or 0),
        canonical_game_id=str(raw["canonical_game_id"]),
        provider_game_id=str(raw.get("provider_game_id") or raw["canonical_game_id"]),
        play_id=str(raw.get("play_id") or raw["provider_play_id"]), provider_play_id=str(raw["provider_play_id"]),
        provider_name=str(raw["provider_name"]), source_artifact_hash=raw.get("source_artifact_hash"),
        canonical_offense_team_id=str(raw["canonical_offense_team_id"]),
        canonical_defense_team_id=str(raw["canonical_defense_team_id"]), quarter=quarter,
        clock=raw.get("clock"), play_sequence=int(raw.get("play_sequence") or 0),
        overtime=bool(raw.get("overtime") or quarter > 4), raw_record_reference=str(raw.get("raw_record_reference") or ""),
        counts_as_official_play=official, no_play=no_play, nullified_by_penalty=nullified,
        duplicate_status=str(raw.get("duplicate_status") or "UNIQUE"), correction_status=str(raw.get("correction_status") or "ORIGINAL"),
        out_of_scope_reason=out_of_scope, offensive_event_family=family, dropback_result=dropback_result,
        pass_attempt_result=pass_result, rush_category=rush_category, quarterback_id=quarterback,
        target_player_id=target_player, receiver_id=receiver, rusher_id=rusher, passer_id=passer,
        increments_dropback=dropback, increments_pass_attempt=pass_attempt, increments_sack=sack,
        increments_target=target, increments_completion=completion, increments_reception=reception,
        increments_official_rush_attempt=rush, receiving_yards=receiving_yards, rushing_yards=rushing_yards,
        sack_yards=sack_yards, non_target_attempt_count=non_target, team_rush_residual_count=team_residual,
    )
    return event, None


def normalize_events(records: Iterable[dict[str, Any]]) -> tuple[list[CanonicalEvent], list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for raw in records:
        key = (str(raw.get("canonical_game_id") or raw.get("game_id") or ""),
               str(raw.get("provider_play_id") or raw.get("play_id") or ""))
        groups.setdefault(key, []).append(raw)
    accepted, quarantined = [], []
    for key in sorted(groups):
        versions = groups[key]
        ordered = sorted(versions, key=lambda row: (int(row.get("correction_version") or 0),
                                                     str(row.get("correction_status") or ""),
                                                     json.dumps(row, sort_keys=True, default=str,
                                                                separators=(",", ":"))))
        winner = ordered[-1]
        if len(versions) > 1 and len({int(row.get("correction_version") or 0) for row in versions}) == 1:
            quarantined.extend(quarantine(row, "DUPLICATE_PLAY", "duplicate play ID has no unique correction winner") for row in versions)
            continue
        for loser in ordered[:-1]:
            quarantined.append(quarantine(loser, "DUPLICATE_PLAY", "superseded provider play version", retained=True))
        event, finding = normalize_event(winner)
        if finding:
            quarantined.append(finding)
        elif event:
            accepted.append(event)
    return sorted(accepted, key=lambda event: (event.canonical_game_id, event.play_sequence, event.play_id)), quarantined
