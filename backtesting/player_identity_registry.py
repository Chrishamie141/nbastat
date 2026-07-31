"""Leakage-safe extraction of game-scoped NFL player identities from disk.

Identity evidence is not participation evidence: this module never manufactures
statistics, usage, outcomes, or grading eligibility.  Source precedence is
provider participant/roster ID, box-score athlete, player stats, injury/roster,
then a deterministic game/team/name fallback.  Matching remains exact.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .game_matching import normalize_team
from .player_identity import first_player_id


SOURCE_PRIORITY = {"provider_participant": 0, "provider_roster": 0,
                   "provider_box_score": 1, "player_stats": 2,
                   "injury_roster": 3, "game_player": 3}
PLAYER_COLLECTIONS = ("athletes", "participants", "roster", "depthchart", "depth_chart",
                      "players", "statistics", "leaders", "injuries")
NAME_PLAYER_COLLECTIONS = tuple(x for x in PLAYER_COLLECTIONS if x != "statistics")
PRODUCTION_CASES = ("James Cook", "Aaron Jones", "Tyler Higbee", "Chigoziem Okonkwo")


def normalize_player_name(value: Any) -> str:
    return " ".join("".join(c for c in str(value or "").casefold()
                            if c.isalnum() or c.isspace()).split())


def _read(path: Path, default: Any = None) -> Any:
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return default


def _team(value: Any) -> str:
    if isinstance(value, dict):
        value = (value.get("abbreviation") or value.get("shortDisplayName") or
                 value.get("displayName") or value.get("name"))
    return normalize_team(value)


def _candidate(value: dict[str, Any], *, game_id: str, season: int, week: int,
               source: str, team: Any = None, timestamps: dict[str, Any] | None = None,
               audit: dict[str, Any] | None = None) -> dict[str, Any] | None:
    athlete = value.get("athlete") if isinstance(value.get("athlete"), dict) else value
    name = (athlete.get("displayName") or athlete.get("fullName") or athlete.get("player_name")
            or athlete.get("player") or athlete.get("name"))
    normalized = normalize_player_name(name)
    membership = _team(value.get("team") or athlete.get("team") or team)
    if not normalized:
        if audit is not None: audit["identities_rejected_missing_name"] += 1
        return None
    if not membership:
        if audit is not None: audit["identities_rejected_missing_team"] += 1
        return None
    if audit is not None and normalized in audit.get("production_case_evidence", {}):
        audit["production_case_evidence"][normalized].add(source)
    provider_id = first_player_id(athlete.get("id"), athlete.get("uid"), athlete.get("athlete_id"),
                                  athlete.get("player_id"), athlete.get("provider_player_id"),
                                  value.get("athlete_id"), value.get("player_id"))
    position = athlete.get("position") or value.get("position")
    if isinstance(position, dict): position = position.get("abbreviation") or position.get("name")
    times = timestamps or {}; canonical = provider_id or f"history:{game_id}:{membership}:{normalized}"
    return {"canonical_player_id": canonical, "provider_player_id": provider_id,
            "player_id": canonical, "player_name": str(name), "normalized_player_name": normalized,
            "team": membership, "game_id": str(game_id), "season": int(season), "week": int(week),
            "position": position, "source": source, "known_at": times.get("known_at"),
            "captured_at": times.get("captured_at"), "data_as_of": times.get("data_as_of"),
            "identity_provenance": [source], "has_stats": source == "player_stats"}


def _source(context: str) -> str | None:
    label=context.casefold()
    if "injur" in label: return "injury_roster"
    if "roster" in label or "depth" in label: return "provider_roster"
    if "participant" in label: return "provider_participant"
    if any(x in label for x in ("boxscore", "box_score", "statistics", "athletes", "players", "leaders")):
        return "provider_box_score"
    return None


def _walk_provider(value: Any, *, game_id: str, season: int, week: int, context: str = "",
                   team: Any = None, timestamps: dict[str, Any] | None = None,
                   audit: dict[str, Any] | None = None):
    """Walk only semantically labelled ESPN player collections, carrying team context."""
    if isinstance(value, list):
        for item in value:
            yield from _walk_provider(item, game_id=game_id, season=season, week=week,
                                      context=context, team=team, timestamps=timestamps, audit=audit)
        return
    if not isinstance(value, dict): return
    local_team=value.get("team") or team; local_times=dict(timestamps or {})
    for key in ("known_at", "captured_at", "data_as_of"):
        if value.get(key): local_times[key]=value[key]
    source=_source(context)
    identity_shape=isinstance(value.get("athlete"), dict) or any(value.get(k) for k in
                   ("fullName", "player_name", "athlete_id", "provider_player_id"))
    # displayName/name alone is accepted only inside a direct player collection;
    # this avoids treating competitor/team metadata as an athlete.
    leaf=context.casefold().split(".")[-1]
    identity_shape = identity_shape or (leaf in NAME_PLAYER_COLLECTIONS and
                                        any(value.get(k) for k in ("displayName", "name")))
    extracted_wrapper=False
    if source and identity_shape:
        if audit is not None: audit["records_inspected_by_source"][source] += 1
        row=_candidate(value, game_id=game_id, season=season, week=week, source=source,
                       team=local_team, timestamps=local_times, audit=audit)
        if row:
            extracted_wrapper=isinstance(value.get("athlete"),dict)
            if audit is not None: audit["identities_extracted_by_source"][source] += 1
            yield row
    for key, child in value.items():
        if extracted_wrapper and key == "athlete":
            continue
        if isinstance(child, (dict, list)):
            yield from _walk_provider(child, game_id=game_id, season=season, week=week,
                context=f"{context}.{key}" if context else str(key), team=local_team,
                timestamps=local_times, audit=audit)


def _merge(rows: Iterable[dict[str, Any]], audit: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    groups=[]
    for row in sorted(rows,key=lambda r:(str(r["game_id"]),r["team"],r["normalized_player_name"],
                                         SOURCE_PRIORITY.get(r["source"],99),str(r.get("provider_player_id") or ""))):
        matches=[g for g in groups if g[0]["game_id"]==row["game_id"] and g[0]["team"]==row["team"] and
                 ((row.get("provider_player_id") and any(x.get("provider_player_id")==row["provider_player_id"] for x in g)) or
                  (g[0]["normalized_player_name"]==row["normalized_player_name"] and
                   (not row.get("provider_player_id") or not any(x.get("provider_player_id") for x in g))))]
        (matches[0].append(row) if matches else groups.append([row]))
    result=[]
    for group in groups:
        best=min(group,key=lambda r:(SOURCE_PRIORITY.get(r["source"],99),0 if r.get("provider_player_id") else 1))
        provider=next((r["provider_player_id"] for r in group if r.get("provider_player_id")),None)
        out=dict(best); out["provider_player_id"]=provider
        out["canonical_player_id"]=out["player_id"]=provider or f"history:{out['game_id']}:{out['team']}:{out['normalized_player_name']}"
        out["has_stats"]=any(r.get("has_stats") for r in group)
        out["identity_provenance"]=sorted({p for r in group for p in r["identity_provenance"]},key=lambda p:SOURCE_PRIORITY.get(p,99))
        out["identity_aliases"]=sorted({(r["normalized_player_name"],r["team"]) for r in group}); result.append(out)
    if audit is not None: audit["duplicate_identities_merged"] = sum(len(g)-1 for g in groups)
    return sorted(result,key=lambda r:(r["game_id"],r["team"],r["normalized_player_name"],r["canonical_player_id"]))


def _aliases(game: dict[str, Any]) -> set[str]:
    keys=("game_id","provider_event_id","source_event_id","raw_event_id","espn_event_id","event_id","id")
    values={str(game[k]) for k in keys if game.get(k) is not None}
    values |= {v.removeprefix("espn-") for v in tuple(values)}
    return values


def _event_units(payload: Any) -> list[Any]:
    """Split multi-event scoreboards so identities cannot leak across games."""
    if isinstance(payload,dict) and isinstance(payload.get("events"),list): return payload["events"]
    return [payload]


def _unit_ids(unit: Any) -> set[str]:
    if not isinstance(unit,dict): return set()
    found=set()
    for obj in (unit, unit.get("header"), unit.get("gameInfo")):
        if isinstance(obj,dict):
            for key in ("id","eventId","event_id","uid"):
                if obj.get(key) is not None: found.add(str(obj[key]).removeprefix("espn-"))
    return found


def _competitor_teams(unit: Any) -> set[str]:
    teams=set()
    def walk(value: Any, parent=""):
        if isinstance(value,list):
            for x in value: walk(x,parent)
        elif isinstance(value,dict):
            if parent=="competitors" and value.get("team"): teams.add(_team(value["team"]))
            for k,v in value.items():
                if k in ("header","competitions","competition","competitors","events"): walk(v,k)
    walk(unit); return {t for t in teams if t}


def _map_unit(unit: Any, games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids=_unit_ids(unit)
    exact=[g for g in games if ids & {x.removeprefix("espn-") for x in _aliases(g)}]
    if len(exact)==1: return exact
    teams=_competitor_teams(unit)
    paired=[g for g in games if teams and teams=={_team(g.get("home_team")),_team(g.get("away_team"))}]
    return paired if len(paired)==1 else []


def build_identity_registry(directory: Path, games: list[dict[str, Any]], *, season: int, week: int,
                            cache_root: Path | None = None,
                            diagnostics: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Build identities exclusively from local artifacts and optionally fill diagnostics."""
    audit={"records_inspected_by_source":Counter(),"identities_extracted_by_source":Counter(),
           "files_containing_athlete_identities":set(),"identities_rejected_missing_team":0,
           "identities_rejected_missing_name":0,"identities_rejected_ambiguous_game_context":0,
           "duplicate_identities_merged":0,
           "production_case_evidence":{normalize_player_name(n):set() for n in PRODUCTION_CASES}}
    rows=[]; game_by_team={}
    for game in games:
        gid=str(game.get("game_id"))
        for team in (game.get("home_team"),game.get("away_team")): game_by_team.setdefault(_team(team),[]).append(gid)
        for player in game.get("players",[]) or []:
            audit["records_inspected_by_source"]["game_player"]+=1
            rec=_candidate(player,game_id=gid,season=season,week=week,source="game_player",audit=audit)
            if rec: rows.append(rec); audit["identities_extracted_by_source"]["game_player"]+=1
        rows.extend(_walk_provider(game,game_id=gid,season=season,week=week,context="game.participants",audit=audit))
    for filename,source in (("player_stats.json","player_stats"),("injuries.json","injury_roster")):
        for value in _read(directory/filename,[]) or []:
            if not isinstance(value,dict): continue
            audit["records_inspected_by_source"][source]+=1
            gids=[str(value["game_id"])] if value.get("game_id") else game_by_team.get(_team(value.get("team")),[])
            if len(gids)!=1: audit["identities_rejected_ambiguous_game_context"]+=1; continue
            rec=_candidate(value,game_id=gids[0],season=season,week=week,source=source,
                           timestamps={k:value.get(k) for k in ("known_at","captured_at","data_as_of")},audit=audit)
            if rec: rows.append(rec); audit["identities_extracted_by_source"][source]+=1
    excluded={"player_stats.json","injuries.json","player_prop_odds.json","player_prop_rebuild_audit.json","player_identities.json"}
    paths=[p for p in directory.rglob("*.json") if p.name not in excluded]
    if cache_root and cache_root.exists():
        paths += [p for p in cache_root.rglob("*.json") if "espn" in str(p).casefold() and
                  (f"week_{week:02d}" in str(p).casefold() or f"week_{week}" in str(p).casefold())]
    for path in sorted(set(paths)):
        payload=_read(path)
        for unit in _event_units(payload):
            mapped=_map_unit(unit,games)
            if len(mapped)!=1:
                # Count only units which actually look capable of containing players.
                if any(k in json.dumps(unit).casefold() for k in ('"athletes"','"participants"','"roster"')):
                    audit["identities_rejected_ambiguous_game_context"]+=1
                continue
            before=len(rows); game=mapped[0]
            rows.extend(_walk_provider(unit,game_id=str(game["game_id"]),season=season,week=week,
                                       context=path.stem,audit=audit))
            if len(rows)>before: audit["files_containing_athlete_identities"].add(str(path))
    merged=_merge(rows,audit)
    if diagnostics is not None:
        final_names={r["normalized_player_name"] for r in merged}
        cases={}
        for display_name in PRODUCTION_CASES:
            normalized=normalize_player_name(display_name); evidence=audit["production_case_evidence"][normalized]
            cases[display_name]={"raw_summary_participant_data":bool(evidence & {"provider_participant"}),
                "roster_boxscore_identity_data":bool(evidence & {"provider_roster","provider_box_score","game_player"}),
                "injuries":bool(evidence & {"injury_roster"}),"player_stats":bool(evidence & {"player_stats"}),
                "final_player_identities":normalized in final_names}
        diagnostics.update({**audit,
            "records_inspected_by_source":dict(sorted(audit["records_inspected_by_source"].items())),
            "identities_extracted_by_source":dict(sorted(audit["identities_extracted_by_source"].items())),
            "files_containing_athlete_identities":sorted(audit["files_containing_athlete_identities"]),
            "production_case_evidence":cases,
            "provider_identities":sum(bool(r.get("provider_player_id")) for r in merged),
            "provider_id_coverage":sum(bool(r.get("provider_player_id")) for r in merged)/len(merged) if merged else 0.0})
    return merged


def registry_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_source=Counter(p for row in rows for p in row.get("identity_provenance",[row.get("source")]))
    coverage=Counter(f"{r['game_id']}|{r['team']}" for r in rows)
    collisions={r["canonical_player_id"]:[list(x) for x in r.get("identity_aliases",[])] for r in rows if len(r.get("identity_aliases",[]))>1}
    return {"identity_registry_players":len(rows),"identity_registry_players_by_source":dict(sorted(by_source.items())),
            "game_team_roster_coverage":dict(sorted(coverage.items())),"identities_with_stats":sum(bool(r.get("has_stats")) for r in rows),
            "identities_without_stats":sum(not r.get("has_stats") for r in rows),"unique_identities_per_game_team":dict(sorted(coverage.items())),
            "provider_identities":sum(bool(r.get("provider_player_id")) for r in rows),
            "provider_id_coverage":sum(bool(r.get("provider_player_id")) for r in rows)/len(rows) if rows else 0.0,
            "identity_collision_count":len(collisions),"identity_collisions":collisions}
