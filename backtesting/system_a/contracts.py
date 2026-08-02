"""Milestone 0 field inventory and canonical semantic definitions."""
from __future__ import annotations

from typing import Any


SCHEMA_VERSION = 1
PROVIDERS = {"espn", "espn-scoreboard", "espn-box-score", "none", "canonical-derived"}
AVAILABILITY = {"AVAILABLE_RAW", "DERIVABLE", "MISSING", "INSUFFICIENT_COVERAGE"}
TEMPORAL = {"PREGAME_SAFE", "POSTGAME_ONLY", "TIMESTAMP_UNVERIFIED", "NOT_APPLICABLE"}
ROLES = {"FEATURE_CANDIDATE", "TRAINING_TARGET", "IDENTIFIER", "RECONCILIATION_FIELD", "CONTEXT_METADATA"}
DOMAINS = {"identifier", "game_context", "play_event", "opportunity", "efficiency",
           "player_outcome", "team_outcome", "audit_only"}


IDENTIFIER_FIELDS = (
    "canonical_game_id", "provider_game_id", "canonical_player_id", "provider_player_id",
    "canonical_team_id", "offense_team_id", "defense_team_id", "season", "week",
    "kickoff_utc", "play_id", "drive_id", "play_sequence", "quarter", "clock",
    "overtime_indicator", "data_provider_name", "data_provider_version",
)
VALIDITY_FIELDS = (
    "counts_as_official_play", "no_play", "penalty_nullified_play", "duplicate_play_indicator",
    "replay_or_correction_indicator", "two_point_attempt", "special_teams_play", "defensive_play",
    "aborted_play", "provider_correction_status",
)
PASS_FIELDS = (
    "dropback", "pass_attempt", "sack", "sack_yards", "qb_scramble", "spike", "throwaway",
    "batted_or_tipped_unassigned_attempt", "interception", "target_player_id", "credited_target",
    "unassigned_non_target_attempt", "completed_pass", "credited_reception", "receiving_yards",
    "lateral_indicator", "provider_lateral_semantics",
)
RUSH_FIELDS = (
    "official_rush_attempt", "rusher_player_id", "designed_rb_wr_rush", "designed_qb_rush",
    "qb_kneel", "team_rush", "aborted_rush_residual", "rushing_yards",
    "provider_kneel_treatment", "provider_scramble_treatment", "provider_rush_lateral_fumble_treatment",
)
OUTCOME_FIELDS = ("targets", "receptions", "rush_attempts")
CONTEXT_FIELDS = (
    "active_inactive_status", "snap_share", "route_share", "targets_per_team_dropback_proxy",
    "injury_status", "injury_report_timestamp", "inactive_status", "depth_chart_status", "starting_qb",
    "starting_qb_continuity", "roof_status", "kickoff_weather", "wind", "precipitation", "temperature",
    "field_surface", "rest_days", "travel_time_zone_context",
)
REQUIRED_LAUNCH_FIELDS = tuple(dict.fromkeys(
    IDENTIFIER_FIELDS + VALIDITY_FIELDS + PASS_FIELDS + RUSH_FIELDS + OUTCOME_FIELDS + CONTEXT_FIELDS
))


def inventory_schema() -> dict[str, Any]:
    required = [
        "canonical_field_name", "display_name", "schema_version", "domain", "role", "data_type",
        "unit", "valid_range", "support", "null_semantics", "zero_semantics", "availability_status",
        "temporal_safety", "primary_provider", "provider_field_name", "provider_semantics",
        "fallback_provider", "derivation_rule", "derivation_dependencies", "derivation_cutoff_rule",
        "seasons_available", "first_available_date", "last_available_date", "game_coverage_count",
        "game_coverage_rate", "player_game_coverage_count", "player_game_coverage_rate",
        "join_success_rate", "leakage_notes", "licensing_notes", "known_anomalies",
        "reconciliation_rule", "required_for_launch", "status_reason", "last_audited_at",
        "source_artifact_hash",
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "nfl-system-a-data-inventory-v1",
        "title": "NFL System A Data Inventory",
        "type": "object", "required": ["schema_version", "fields"],
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION},
            "fields": {"type": "array", "items": {
                "type": "object", "required": required, "additionalProperties": False,
                "properties": {
                    **{name: {} for name in required},
                    "canonical_field_name": {"type": "string", "minLength": 1},
                    "schema_version": {"const": SCHEMA_VERSION},
                    "domain": {"enum": sorted(DOMAINS)}, "role": {"enum": sorted(ROLES)},
                    "availability_status": {"enum": sorted(AVAILABILITY)},
                    "temporal_safety": {"enum": sorted(TEMPORAL)},
                    "primary_provider": {"enum": sorted(PROVIDERS)},
                    "required_for_launch": {"type": "boolean"},
                    "derivation_dependencies": {"type": "array", "items": {"type": "string"}},
                    "seasons_available": {"type": "array", "items": {"type": "integer"}},
                    "known_anomalies": {"type": "array", "items": {"type": "string"}},
                },
            }},
        }, "additionalProperties": False,
    }


def _entry(name: str, *, domain: str, role: str, data_type: str = "number", unit: str | None = None,
           availability: str = "MISSING", temporal: str = "NOT_APPLICABLE", provider: str = "none",
           provider_field: str | None = None, semantics: str = "Not present in stored repository artifacts.",
           derivation: str | None = None, dependencies: list[str] | None = None,
           cutoff: str | None = None, reconciliation: str = "No reconciliation available without source data.",
           nulls: str = "null means unknown or unavailable; it must not be interpreted as zero",
           zeroes: str = "zero is a known measured zero only when supplied by the provider",
           support: str | None = None, valid_range: str | None = None,
           status_reason: str = "Source not stored", required: bool = True) -> dict[str, Any]:
    return {
        "canonical_field_name": name, "display_name": name.replace("_", " ").title(),
        "schema_version": SCHEMA_VERSION, "domain": domain, "role": role, "data_type": data_type,
        "unit": unit, "valid_range": valid_range, "support": support, "null_semantics": nulls,
        "zero_semantics": zeroes, "availability_status": availability, "temporal_safety": temporal,
        "primary_provider": provider, "provider_field_name": provider_field,
        "provider_semantics": semantics, "fallback_provider": None, "derivation_rule": derivation,
        "derivation_dependencies": dependencies or [], "derivation_cutoff_rule": cutoff,
        "seasons_available": [], "first_available_date": None, "last_available_date": None,
        "game_coverage_count": 0, "game_coverage_rate": None, "player_game_coverage_count": 0,
        "player_game_coverage_rate": None, "join_success_rate": None,
        "leakage_notes": "Postgame values may become future-game history only after their source game ended.",
        "licensing_notes": "Provider export retained locally; downstream licensing not independently verified.",
        "known_anomalies": [], "reconciliation_rule": reconciliation,
        "required_for_launch": required, "status_reason": status_reason,
        "last_audited_at": None, "source_artifact_hash": None,
    }


def base_inventory() -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    game_raw = {"canonical_game_id", "provider_game_id", "season", "week", "kickoff_utc"}
    for name in IDENTIFIER_FIELDS:
        available = name in game_raw or name in {"canonical_player_id", "provider_player_id", "canonical_team_id"}
        fields.append(_entry(
            name, domain="identifier", role="IDENTIFIER",
            data_type="integer" if name in {"season", "week", "play_sequence", "quarter"} else "string",
            availability="AVAILABLE_RAW" if available else "MISSING",
            temporal="PREGAME_SAFE" if name in game_raw else "NOT_APPLICABLE",
            provider="espn-scoreboard" if name in game_raw else "espn-box-score" if available else "none",
            provider_field={"canonical_game_id": "game_id", "provider_game_id": "espn_event_id",
                            "kickoff_utc": "kickoff_time"}.get(name, name),
            semantics="ESPN scoreboard/game or box-score identifier retained in frozen snapshots." if available else "Requires play-by-play source not stored.",
            reconciliation="Exact canonical registry join; ambiguous mappings are quarantined.",
            status_reason="Stored in weekly snapshots" if available else "No play-by-play source stored",
        ))
    for name in VALIDITY_FIELDS + PASS_FIELDS + RUSH_FIELDS:
        unit = "yards" if name.endswith("yards") else "count" if name not in {
            "target_player_id", "rusher_player_id", "provider_lateral_semantics",
            "provider_kneel_treatment", "provider_scramble_treatment", "provider_rush_lateral_fumble_treatment",
            "provider_correction_status",
        } else None
        fields.append(_entry(
            name, domain="play_event", role="RECONCILIATION_FIELD",
            data_type="boolean" if name in VALIDITY_FIELDS or name in {
                "dropback", "pass_attempt", "sack", "qb_scramble", "spike", "throwaway",
                "batted_or_tipped_unassigned_attempt", "interception", "credited_target",
                "unassigned_non_target_attempt", "completed_pass", "credited_reception", "lateral_indicator",
                "official_rush_attempt", "designed_rb_wr_rush", "designed_qb_rush", "qb_kneel",
                "team_rush", "aborted_rush_residual",
            } else "string" if "id" in name or "semantics" in name or "treatment" in name else "number",
            unit=unit, availability="MISSING", temporal="POSTGAME_ONLY", provider="none",
            semantics="Canonical play-event field; current repository has no stored play-by-play provider export.",
            reconciliation="Aggregated canonical events must reconcile to official box-score totals.",
            status_reason="No play-by-play, drive, or event artifacts found for 2023-2025",
        ))
    for name in OUTCOME_FIELDS + ("receiving_yards", "rushing_yards"):
        # receiving_yards/rushing_yards already have event entries; update them
        existing = next((row for row in fields if row["canonical_field_name"] == name), None)
        if existing:
            existing.update({"availability_status": "AVAILABLE_RAW", "temporal_safety": "POSTGAME_ONLY",
                             "primary_provider": "espn-box-score", "provider_field_name": name,
                             "provider_semantics": "Official player box-score outcome for the completed game.",
                             "domain": "player_outcome", "role": "TRAINING_TARGET",
                             "status_reason": "Stored in player_stats.json completed_game_history rows"})
            continue
        fields.append(_entry(
            name, domain="player_outcome", role="TRAINING_TARGET", data_type="number", unit="count",
            valid_range=">= 0", availability="AVAILABLE_RAW", temporal="POSTGAME_ONLY",
            provider="espn-box-score", provider_field=name,
            semantics="Official player box-score outcome for the completed game.",
            reconciliation="Must equal the player total aggregated from accepted canonical events.",
            status_reason="Stored in player_stats.json completed_game_history rows",
        ))
    for name in CONTEXT_FIELDS:
        status = "DERIVABLE" if name == "targets_per_team_dropback_proxy" else "MISSING"
        temporal = "TIMESTAMP_UNVERIFIED" if name in {"injury_status", "inactive_status", "depth_chart_status", "starting_qb"} else "NOT_APPLICABLE"
        fields.append(_entry(
            name, domain="game_context", role="FEATURE_CANDIDATE", data_type="number" if name in {
                "snap_share", "route_share", "targets_per_team_dropback_proxy", "wind", "temperature", "rest_days"
            } else "string", availability=status, temporal=temporal, provider="canonical-derived" if status == "DERIVABLE" else "none",
            derivation="credited player targets / canonical team dropbacks" if name == "targets_per_team_dropback_proxy" else None,
            dependencies=["targets", "dropback"] if name == "targets_per_team_dropback_proxy" else [],
            cutoff="All source games/events must end strictly before forecast cutoff." if status == "DERIVABLE" else None,
            semantics=("A target-frequency proxy. It is explicitly not route share." if name == "targets_per_team_dropback_proxy"
                       else "Unavailable in stored historical snapshots or lacks verified historical publication timing."),
            status_reason=("Derivable only after canonical play-by-play becomes available" if status == "DERIVABLE"
                           else "No verified nonempty source coverage in stored snapshots"),
            reconciliation="Context metadata is not an official player-stat total.",
        ))
    # One entry per canonical name, preserving the outcome semantics where a
    # play-level and player-outcome name overlap.
    deduplicated = {row["canonical_field_name"]: row for row in fields}
    return {"schema_version": SCHEMA_VERSION,
            "fields": [deduplicated[name] for name in sorted(deduplicated)]}


def canonical_definitions(inventory: dict[str, Any]) -> dict[str, Any]:
    definitions = []
    for field in inventory["fields"]:
        name = field["canonical_field_name"]
        definitions.append({
            "canonical_field_name": name, "schema_version": SCHEMA_VERSION,
            "football_meaning": field["provider_semantics"],
            "provider_mappings": [{"provider": field["primary_provider"],
                                   "field": field["provider_field_name"],
                                   "semantics": field["provider_semantics"]}],
            "inclusion_rules": "Include only records within the field domain that pass identity and validity gates.",
            "exclusion_rules": "Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.",
            "null_handling": field["null_semantics"],
            "corrections_and_deduplication": "Use the deterministic accepted correction; quarantine unresolved duplicate versions.",
            "overtime": "Included in official game totals and marked explicitly on canonical events.",
            "two_point_attempts": "Represented but excluded from ordinary official player-stat effects.",
            "spikes": "Official pass attempts with no credited target.",
            "throwaways": "Official pass attempts with no credited target.",
            "batted_tipped_passes": "Unassigned unless the provider credits an intended target.",
            "sacks": "Dropbacks, not pass attempts; sack yards remain separate from player passing yards.",
            "scrambles": "One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.",
            "kneels": "One KNEEL event; canonical policy follows provider-normalized official rushing totals.",
            "aborted_plays": "Team/aborted residual unless provider semantics resolve a player official rush.",
            "team_rushes": "Retained in team residual; never forced onto a player.",
            "laterals": "Quarantined when provider receiving/rushing allocation cannot be resolved.",
            "provider_corrections": "Latest unambiguous correction wins by deterministic version ordering.",
            "reconciliation_expectation": field["reconciliation_rule"],
        })
    return {"schema_version": SCHEMA_VERSION, "provider_scope": ["espn-scoreboard", "espn-box-score"],
            "definitions": definitions}
