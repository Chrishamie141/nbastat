"""Leakage-safe, game-scoped NFL player identity registry.

Identity is deliberately independent of statistical participation.  This module
only reads snapshot artifacts (and an optional historical raw cache); it never
contacts a provider and never creates statistical rows.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .game_matching import normalize_team
from .player_identity import first_player_id


SOURCE_PRIORITY = {
    "provider_participant": 0,
    "provider_roster": 0,
    "provider_box_score": 1,
    "player_stats": 2,
    "injury_roster": 3,
    "game_player": 3,
}


def normalize_player_name(value: Any) -> str:
    return " ".join("".join(c for c in str(value or "").casefold()
                            if c.isalnum() or c.isspace()).split())


def _read(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _team(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("abbreviation") or value.get("shortDisplayName") or value.get("displayName") or value.get("name")
    return normalize_team(value)


def _candidate(value: dict[str, Any], *, game_id: str, season: int, week: int,
               source: str, team: Any = None, timestamps: dict[str, Any] | None = None) -> dict[str, Any] | None:
    athlete = value.get("athlete") if isinstance(value.get("athlete"), dict) else value
    name = (athlete.get("displayName") or athlete.get("fullName") or athlete.get("player_name")
            or athlete.get("player") or athlete.get("name"))
    normalized = normalize_player_name(name)
    membership = _team(value.get("team") or athlete.get("team") or team)
    if not normalized or not membership:
        return None
    provider_id = first_player_id(athlete.get("id"), athlete.get("uid"), athlete.get("athlete_id"),
                                  athlete.get("player_id"), athlete.get("provider_player_id"),
                                  value.get("athlete_id"), value.get("player_id"))
    position = athlete.get("position") or value.get("position")
    if isinstance(position, dict): position = position.get("abbreviation") or position.get("name")
    times = timestamps or {}
    canonical = provider_id or f"history:{game_id}:{membership}:{normalized}"
    return {"canonical_player_id": canonical, "provider_player_id": provider_id,
            "player_id": canonical, "player_name": str(name), "normalized_player_name": normalized,
            "team": membership, "game_id": str(game_id), "season": int(season), "week": int(week),
            "position": position, "source": source, "known_at": times.get("known_at"),
            "captured_at": times.get("captured_at"), "data_as_of": times.get("data_as_of"),
            "identity_provenance": [source], "has_stats": source == "player_stats"}


def _walk_provider(value: Any, *, game_id: str, season: int, week: int,
                   context: str = "", team: Any = None, timestamps: dict[str, Any] | None = None):
    """Extract identities only from semantically labelled ESPN participant data."""
    if isinstance(value, list):
        for item in value:
            yield from _walk_provider(item, game_id=game_id, season=season, week=week,
                                      context=context, team=team, timestamps=timestamps)
        return
    if not isinstance(value, dict): return
    local_team = value.get("team") or team
    local_times = dict(timestamps or {})
    for key in ("known_at", "captured_at", "data_as_of"):
        if value.get(key): local_times[key] = value[key]
    label = context.casefold()
    source = ("provider_roster" if "roster" in label or "depth" in label else
              "provider_participant" if "participant" in label else
              "provider_box_score" if any(x in label for x in ("boxscore", "box_score", "statistics", "players")) else None)
    if source and (isinstance(value.get("athlete"), dict) or any(value.get(k) for k in ("displayName", "fullName", "player_name"))):
        row = _candidate(value, game_id=game_id, season=season, week=week, source=source,
                         team=local_team, timestamps=local_times)
        if row: yield row
    for key, child in value.items():
        if isinstance(child, (dict, list)):
            child_context = f"{context}.{key}" if context else str(key)
            yield from _walk_provider(child, game_id=game_id, season=season, week=week,
                                      context=child_context, team=local_team, timestamps=local_times)


def _merge(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    for row in sorted(rows, key=lambda r: (str(r["game_id"]), r["team"], r["normalized_player_name"],
                                           SOURCE_PRIORITY.get(r["source"], 99), str(r.get("provider_player_id") or ""))):
        # A provider ID is authoritative.  A lower-quality name-only record may
        # upgrade to it, but two distinct provider IDs are never collapsed.
        matches = [g for g in groups if g[0]["game_id"] == row["game_id"] and g[0]["team"] == row["team"] and
                   ((row.get("provider_player_id") and any(x.get("provider_player_id") == row["provider_player_id"] for x in g)) or
                    (g[0]["normalized_player_name"] == row["normalized_player_name"] and
                     (not row.get("provider_player_id") or not any(x.get("provider_player_id") for x in g))))]
        if matches:
            matches[0].append(row)
        else:
            groups.append([row])
    result=[]
    for group in groups:
        best=min(group, key=lambda r:(SOURCE_PRIORITY.get(r["source"],99), 0 if r.get("provider_player_id") else 1))
        provider=next((r["provider_player_id"] for r in group if r.get("provider_player_id")),None)
        out=dict(best); out["provider_player_id"]=provider
        out["canonical_player_id"]=out["player_id"]=provider or f"history:{out['game_id']}:{out['team']}:{out['normalized_player_name']}"
        out["has_stats"]=any(r.get("has_stats") for r in group)
        out["identity_provenance"]=sorted({p for r in group for p in r["identity_provenance"]},key=lambda p:SOURCE_PRIORITY.get(p,99))
        out["identity_aliases"]=sorted({(r["normalized_player_name"],r["team"]) for r in group})
        result.append(out)
    return sorted(result,key=lambda r:(r["game_id"],r["team"],r["normalized_player_name"],r["canonical_player_id"]))


def build_identity_registry(directory: Path, games: list[dict[str, Any]], *, season: int, week: int,
                            cache_root: Path | None = None) -> list[dict[str, Any]]:
    """Build identities exclusively from artifacts already present on disk."""
    rows=[]; game_by_team={}
    for game in games:
        gid=str(game.get("game_id"));
        for team in (game.get("home_team"),game.get("away_team")): game_by_team.setdefault(_team(team),[]).append(gid)
        for player in game.get("players",[]) or []:
            rec=_candidate(player,game_id=gid,season=season,week=week,source="game_player")
            if rec: rows.append(rec)
        rows.extend(_walk_provider(game,game_id=gid,season=season,week=week,context="game.participants"))
    for filename, source in (("player_stats.json","player_stats"),("injuries.json","injury_roster")):
        for value in _read(directory/filename,[]) or []:
            if not isinstance(value,dict): continue
            gids=[str(value["game_id"])] if value.get("game_id") else game_by_team.get(_team(value.get("team")),[])
            for gid in gids:
                rec=_candidate(value,game_id=gid,season=season,week=week,source=source,
                               timestamps={k:value.get(k) for k in ("known_at","captured_at","data_as_of")})
                if rec: rows.append(rec)
    # Summary/roster files inside the snapshot are local historical evidence.
    paths=[p for p in directory.rglob("*.json") if p.name not in {"player_stats.json","injuries.json","player_prop_odds.json","player_prop_rebuild_audit.json"}]
    if cache_root and cache_root.exists():
        paths += [p for p in cache_root.rglob("*.json") if "espn" in str(p).casefold() and (f"week_{week:02d}" in str(p) or f"week_{week}" in str(p))]
    for path in sorted(set(paths)):
        payload=_read(path)
        if payload is None: continue
        for game in games:
            gid=str(game.get("game_id")); provider=str(game.get("provider_event_id") or "")
            text=json.dumps(payload,sort_keys=True)
            if len(games)>1 and gid not in text and (not provider or provider not in text): continue
            rows.extend(_walk_provider(payload,game_id=gid,season=season,week=week,context=path.stem))
    return _merge(rows)


def registry_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_source=Counter(p for row in rows for p in row.get("identity_provenance",[row.get("source")]))
    coverage=Counter(f"{r['game_id']}|{r['team']}" for r in rows)
    collisions={r["canonical_player_id"]:[list(x) for x in r.get("identity_aliases",[])] for r in rows
                if len(r.get("identity_aliases",[])) > 1}
    return {"identity_registry_players":len(rows), "identity_registry_players_by_source":dict(sorted(by_source.items())),
            "game_team_roster_coverage":dict(sorted(coverage.items())),
            "identities_with_stats":sum(bool(r.get("has_stats")) for r in rows),
            "identities_without_stats":sum(not r.get("has_stats") for r in rows),
            "unique_identities_per_game_team":dict(sorted(coverage.items())),
            "identity_collision_count":len(collisions),"identity_collisions":collisions}
