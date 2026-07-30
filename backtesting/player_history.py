"""Canonical, leakage-safe NFL player-game history.

Provider snapshots remain immutable.  This module is the single boundary that
turns provider-shaped (including ESPN's category-split ``stats`` rows) data
into observations consumed by simulations and outcome graders.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta
import re
from typing import Any, Iterable

from .game_matching import normalize_team, parse_dt
from .team_history import prediction_cutoff
from .player_identity import normalize_player_id

STAT_FIELDS = ("passing_attempts", "completions", "passing_yards", "passing_tds",
               "rushing_attempts", "rushing_yards", "rushing_tds", "targets",
               "receptions", "receiving_yards", "receiving_tds")
STAT_ALIASES = {
    "attempts": "context_attempts", "passing_attempts": "passing_attempts",
    "pass_attempts": "passing_attempts", "completions": "completions",
    "passing_yards": "passing_yards", "passing_touchdowns": "passing_tds",
    "passing_tds": "passing_tds", "rushing_attempts": "rushing_attempts",
    "rush_attempts": "rushing_attempts", "carries": "rushing_attempts",
    "rushing_yards": "rushing_yards", "rushing_touchdowns": "rushing_tds",
    "rushing_tds": "rushing_tds", "targets": "targets", "receptions": "receptions",
    "receiving_yards": "receiving_yards", "receiving_touchdowns": "receiving_tds",
    "receiving_tds": "receiving_tds",
}
REJECTION_REASONS = ("irrelevant_team", "future_timestamp", "unknown_timestamp",
    "target_game_row", "missing_player_identity", "missing_team_identity",
    "unsupported_record_role", "missing_stat_values", "schema_mismatch", "other")


def normalize_position(value: Any) -> str:
    value = str(value or "UNKNOWN").upper().strip()
    aliases = {"HB": "RB", "FB": "RB", "TB": "RB", "WIDE RECEIVER": "WR",
               "TIGHT END": "TE", "QUARTERBACK": "QB", "RUNNING BACK": "RB"}
    value = aliases.get(value, value)
    return value if value in {"QB", "RB", "WR", "TE"} else "UNKNOWN"


def _name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")


def player_game_identity(row: dict[str, Any]) -> tuple[str, str, str]:
    """Provider ID first; controlled name fallback remains team-scoped."""
    player = normalize_player_id(row.get("player_id")) or ""
    if not player:
        player = f"name:{_name(row.get('player_name') or row.get('player'))}"
    return str(row.get("game_id") or ""), normalize_team(row.get("team")), player


def history_known_at(row: dict[str, Any]):
    values = []
    for field in ("completed_at", "data_as_of", "captured_at", "known_at"):
        if row.get(field) not in (None, ""):
            parsed = parse_dt(row[field])
            if parsed is None:
                return None
            values.append(parsed)
    return max(values) if values else None


def _number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", ""))
        return int(result) if result.is_integer() else result
    except (TypeError, ValueError):
        return None


def _stats(row: dict[str, Any]) -> dict[str, int | float]:
    raw = dict(row.get("stats") or {})
    raw.update({key: row[key] for key in STAT_FIELDS if key in row})
    result = {}
    for key, value in raw.items():
        canonical = STAT_ALIASES.get(str(key).casefold())
        if canonical == "context_attempts":
            if any(k in raw for k in ("passing_yards", "passing_touchdowns", "completions")):
                canonical = "passing_attempts"
            elif any(k in raw for k in ("rushing_yards", "rushing_touchdowns")):
                canonical = "rushing_attempts"
        number = _number(value)
        if canonical in STAT_FIELDS and number is not None:
            result[canonical] = number
    return result


def canonicalize_player_history(rows: Iterable[dict[str, Any]], *, league: str = "nfl",
                                games: dict[str, dict[str, Any]] | None = None
                                ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Combine category-split rows deterministically into player/game rows.

    Equal duplicate values are harmless. Conflicts reject that field rather
    than silently selecting a provider value.
    """
    games = games or {}; grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    rejected = Counter(); collisions: dict[str, set[str]] = defaultdict(set)
    for raw in rows:
        row = dict(raw); stats = _stats(row)
        if not (normalize_player_id(row.get("player_id")) or row.get("player") or row.get("player_name")):
            rejected["missing_player_identity"] += 1; continue
        if not row.get("team"):
            rejected["missing_team_identity"] += 1; continue
        if not stats:
            rejected["missing_stat_values"] += 1; continue
        identity = player_game_identity(row)
        if not identity[0]:
            rejected["schema_mismatch"] += 1; continue
        row["_canonical_stats"] = stats; grouped[identity].append(row)
        if normalize_player_id(row.get("player_id")):
            collisions[normalize_player_id(row["player_id"])].add(_name(row.get("player_name") or row.get("player")))
    observations=[]; conflicts=[]
    for identity, parts in sorted(grouped.items()):
        first=parts[0]; game=games.get(identity[0], {})
        values: dict[str, set[int | float]] = defaultdict(set)
        for part in parts:
            for field, value in part["_canonical_stats"].items(): values[field].add(value)
        stats={field: next(iter(vals)) for field, vals in values.items() if len(vals)==1}
        for field, vals in values.items():
            if len(vals)>1: conflicts.append({"identity": identity, "field": field, "values": sorted(vals)})
        # Legacy ESPN rows mislabeled the target box score as pregame at kickoff.
        # Its public feed provides no finalization time; kickoff + 6h is the same
        # conservative availability policy used for team completed-game history.
        kickoff=parse_dt(game.get("kickoff_time"))
        completed=(kickoff+timedelta(hours=6)) if kickoff else parse_dt(first.get("completed_at"))
        known=completed or history_known_at(first)
        timestamp=known.isoformat().replace("+00:00", "Z") if known else None
        player_id=normalize_player_id(first.get("player_id")) or identity[2]
        position=normalize_position(first.get("position")); position_source="provider"
        if position == "UNKNOWN":
            # Legacy snapshots discarded ESPN's position. A conservative,
            # explicit statistical-role fallback is necessary to keep those
            # immutable snapshots usable; passing wins, then receiving, then rushing.
            if any(k in stats for k in ("passing_attempts","completions","passing_yards","passing_tds")): position="QB"
            elif any(k in stats for k in ("targets","receptions","receiving_yards","receiving_tds")): position="WR"
            elif any(k in stats for k in ("rushing_attempts","rushing_yards","rushing_tds")): position="RB"
            position_source="statistical_role_fallback" if position != "UNKNOWN" else "unknown"
        observation={"league": str(first.get("league") or league).lower(),
            "season": int(first.get("season") or game.get("season")) if (first.get("season") or game.get("season")) is not None else None,
            "week": int(first.get("week") or game.get("week")) if (first.get("week") or game.get("week")) is not None else None,
            "game_id": identity[0], "player_id": player_id,
            "player_name": str(first.get("player_name") or first.get("player") or player_id),
            "team": identity[1], "opponent": normalize_team(first.get("opponent")) if first.get("opponent") else None,
            "position": position, "position_source": position_source, "completed_at": timestamp,
            "data_as_of": timestamp, "captured_at": timestamp, "known_at": timestamp,
            "record_role": "completed_game_history", "source": first.get("source") or "unknown",
            "is_pregame": False, **stats}
        observations.append(observation)
    return observations, {"provider_rows": len(list(rows)) if isinstance(rows, list) else sum(len(v) for v in grouped.values()),
        "canonical_observations": len(observations), "rejections": {r: rejected[r] for r in REJECTION_REASONS},
        "conflicts": conflicts, "identity_collisions": {k: sorted(v) for k,v in collisions.items() if len(v)>1}}


@dataclass(frozen=True)
class PlayerHistoryFilter:
    rows: list[dict[str, Any]]
    loaded: int
    rejection_histogram: dict[str, int]
    latest_timestamp: str | None
    @property
    def rejected_future(self): return self.rejection_histogram["future_timestamp"]
    @property
    def rejected_unknown_timestamp(self): return self.rejection_histogram["unknown_timestamp"]
    @property
    def rejected_other(self): return self.loaded-len(self.rows)-self.rejected_future-self.rejected_unknown_timestamp
    @property
    def rejected_rows(self): return []


def filter_player_history(game: dict[str, Any], rows: Iterable[dict[str, Any]], *, target_teams_only: bool = True) -> PlayerHistoryFilter:
    cutoff=prediction_cutoff(game)
    if cutoff is None: raise ValueError("target game has no valid prediction cutoff")
    teams={normalize_team(game.get("home_team")), normalize_team(game.get("away_team"))}
    target=str(game.get("game_id") or ""); accepted=[]; reasons=Counter(); latest=None; rows=list(rows)
    for row in rows:
        reason=None; known=history_known_at(row)
        if not (normalize_player_id(row.get("player_id")) or row.get("player_name")): reason="missing_player_identity"
        elif not row.get("team"): reason="missing_team_identity"
        elif not any(row.get(f) is not None for f in STAT_FIELDS): reason="missing_stat_values"
        elif row.get("record_role") != "completed_game_history": reason="unsupported_record_role"
        elif known is None: reason="unknown_timestamp"
        elif known >= cutoff: reason="future_timestamp"
        elif target and str(row.get("game_id")) == target: reason="target_game_row"
        elif target_teams_only and normalize_team(row.get("team")) not in teams: reason="irrelevant_team"
        if reason: reasons[reason]+=1; continue
        accepted.append(row); latest=known if latest is None or known>latest else latest
    return PlayerHistoryFilter(accepted,len(rows),{r:reasons[r] for r in REJECTION_REASONS},
        latest.isoformat().replace("+00:00","Z") if latest else None)


def extract_player_outcome(rows: Iterable[dict[str, Any]], game_id: str, player_id: str) -> dict[str, Any] | None:
    return next((r for r in rows if str(r.get("game_id"))==str(game_id) and
                 normalize_player_id(r.get("player_id"))==normalize_player_id(player_id)), None)
