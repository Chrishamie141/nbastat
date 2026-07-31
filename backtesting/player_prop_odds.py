"""Offline-first NFL historical player-prop pricing primitives.

This module never performs I/O. Provider acquisition is deliberately separated
from normalization, reconciliation, cutoff filtering, pricing, and grading.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from statistics import median
from typing import Any, Iterable
import json

from .game_matching import normalize_team
from .markets import CANONICAL_PLAYER_PROP_MARKETS, normalize_player_prop_market
from .team_history import filter_market_quotes, prediction_cutoff
from .game_matching import parse_dt
from .player_identity import first_player_id, normalize_player_id


def decimal_from_american(price: int | float) -> float:
    price = float(price)
    if price == 0: raise ValueError("american odds cannot be zero")
    return 1 + (price / 100 if price > 0 else 100 / abs(price))


def american_from_decimal(price: float) -> int:
    if price <= 1: raise ValueError("decimal odds must be greater than one")
    return round((price - 1) * 100 if price >= 2 else -100 / (price - 1))


@dataclass(frozen=True)
class PlayerPropQuote:
    league: str; season: int; week: int; game_id: str
    canonical_player_id: str; player_name: str; team: str
    market: str; selection: str; line: float
    american_odds: int; decimal_odds: float; implied_probability: float
    bookmaker: str; snapshot_timestamp: str; market_last_update: str
    data_as_of: str; source: str; provider_event_id: str

    def __post_init__(self):
        if self.market not in CANONICAL_PLAYER_PROP_MARKETS: raise ValueError(f"unsupported market: {self.market}")
        if self.selection not in {"OVER", "UNDER"}: raise ValueError("selection must be OVER or UNDER")
        if normalize_player_id(self.canonical_player_id) is None: raise ValueError("canonical_player_id is required")

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class Reconciliation:
    status: str
    canonical_player_id: str | None = None
    player: dict[str, Any] | None = None
    candidate_count: int = 0


EXACT_PROVIDER_ID = "EXACT_PROVIDER_ID"
EXACT_NAME_TEAM_GAME = "EXACT_NAME_TEAM_GAME"
EXACT_NAME_TEAM = "EXACT_NAME_TEAM"
AMBIGUOUS = "AMBIGUOUS"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PlayerHistoryIndex:
    """Immutable game-scoped player history; never cached by name alone."""
    by_game: dict[str, tuple[dict[str, Any], ...]]

    @classmethod
    def build(cls, players: Iterable[dict[str, Any]]) -> "PlayerHistoryIndex":
        grouped: dict[str,list[dict[str,Any]]] = {}
        for player in players: grouped.setdefault(str(player.get("game_id") or ""),[]).append(player)
        return cls({game:tuple(sorted(rows,key=lambda p:(_name(p.get("player_name") or p.get("name") or p.get("player")),normalize_team(p.get("team")),first_player_id(p.get("player_id"),p.get("canonical_player_id"),p.get("athlete_id")) or ""))) for game,rows in grouped.items()})

    def players_for_game(self, game_id: str) -> tuple[dict[str, Any], ...]:
        return self.by_game.get(str(game_id),())

    def identity_records_for_game(self, game_id: str) -> list[dict[str, Any]]:
        """Expose auditable identity metadata without any statistical values."""
        return [{"canonical_player_id":_player_id(player,game_id=str(game_id)),
                 "provider_player_id":first_player_id(player.get("player_id"),player.get("athlete_id")),
                 "normalized_name":_name(player.get("player_name") or player.get("name") or player.get("player")),
                 "team":normalize_team(player.get("team")),"position":player.get("position")}
                for player in self.players_for_game(game_id)]


def build_player_history_index(players: Iterable[dict[str, Any]]) -> PlayerHistoryIndex:
    return PlayerHistoryIndex.build(players)


def _name(value: Any) -> str:
    return " ".join("".join(c for c in str(value or "").casefold() if c.isalnum() or c.isspace()).split())


def _player_id(player: dict[str, Any], *, game_id: str) -> str:
    """Return a stable ID without ever turning a missing ID into ``"None"``.

    Some historical ESPN rows have no athlete ID.  Their controlled fallback
    is deliberately scoped by normalized name, historical team membership and
    game.  This is a canonical history identity, not a global display-name ID.
    """
    supplied = first_player_id(player.get("player_id"), player.get("canonical_player_id"), player.get("athlete_id"))
    if supplied is not None: return supplied
    return "history:%s:%s:%s" % (str(game_id), normalize_team(player.get("team")), _name(player.get("player_name") or player.get("name") or player.get("player")))


def reconcile_player(provider_player: dict[str, Any], canonical_players: Iterable[dict[str, Any]] | PlayerHistoryIndex, *,
                     game_id: str, team: str | None = None) -> Reconciliation:
    """Resolve identity conservatively using ID, game, name and membership."""
    index=canonical_players if isinstance(canonical_players,PlayerHistoryIndex) else build_player_history_index(canonical_players)
    players = list(index.players_for_game(game_id))
    provider_id = first_player_id(provider_player.get("canonical_player_id"), provider_player.get("player_id"), provider_player.get("athlete_id"), provider_player.get("provider_player_id"))
    stable = [p for p in players if provider_id and _player_id(p,game_id=game_id) == provider_id]
    # The Odds API uses ``name`` for the side (Over/Under) and ``description``
    # for the participant, so description intentionally precedes name.
    candidates = stable or [p for p in players if _name(p.get("player_name") or p.get("name") or p.get("player")) == _name(provider_player.get("player_name") or provider_player.get("description") or provider_player.get("name"))]
    if not candidates: return Reconciliation(UNKNOWN,candidate_count=0)
    wanted_team = normalize_team(team or provider_player.get("team"))
    if wanted_team:
        matching = [p for p in candidates if normalize_team(p.get("team")) == wanted_team]
        if not matching: return Reconciliation(UNKNOWN,candidate_count=0)
        candidates = matching
    # Category-split history can contain repeated records for the same player.
    # Collapse only records that resolve to the same non-null canonical ID.
    by_id: dict[str,list[dict[str,Any]]] = {}
    for candidate in candidates: by_id.setdefault(_player_id(candidate,game_id=game_id),[]).append(candidate)
    if len(by_id) != 1: return Reconciliation(AMBIGUOUS,candidate_count=len(by_id))
    canonical_id, copies = next(iter(by_id.items())); player=dict(copies[0])
    player["player_id"] = canonical_id
    status = EXACT_PROVIDER_ID if stable else EXACT_NAME_TEAM_GAME if wanted_team else EXACT_NAME_TEAM
    return Reconciliation(status, canonical_id, player, 1)


def normalize_provider_outcomes(event: dict[str, Any], *, league: str, season: int, week: int,
                                game_id: str, canonical_players: Iterable[dict[str, Any]],
                                snapshot_timestamp: str, source: str = "the-odds-api-historical",
                                requested_snapshot_timestamp: str | None = None,
                                captured_at: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Flatten a provider event while preserving every book/line/side quote."""
    rows, rejected = [], []; player_index=build_player_history_index(canonical_players)
    for book in event.get("bookmakers", []) or []:
        for raw_market in book.get("markets", []) or []:
            market = normalize_player_prop_market(raw_market.get("key"))
            for outcome in raw_market.get("outcomes", []) or []:
                if not market:
                    rejected.append({"reason": "unsupported_market", "market": raw_market.get("key")}); continue
                rec = reconcile_player(outcome, player_index, game_id=game_id, team=outcome.get("team"))
                if rec.status not in {EXACT_PROVIDER_ID,EXACT_NAME_TEAM_GAME,EXACT_NAME_TEAM}:
                    reason="ambiguous_player" if rec.status == AMBIGUOUS else "unknown_player"
                    rejected.append({"reason":reason,"market":market,"player":outcome.get("description"),"candidate_count":rec.candidate_count}); continue
                try:
                    line = float(outcome["point"])
                except (KeyError, TypeError, ValueError) as exc:
                    rejected.append({"reason":"malformed_line","market":market,"player":outcome.get("description"),"detail":str(exc)}); continue
                try:
                    american = int(outcome["price"]); decimal = decimal_from_american(american)
                except (KeyError, TypeError, ValueError) as exc:
                    rejected.append({"reason":"malformed_price","market":market,"player":outcome.get("description"),"detail":str(exc)}); continue
                row = PlayerPropQuote(
                    league.lower(), int(season), int(week), str(game_id), rec.canonical_player_id,
                    str((rec.player or {}).get("player_name") or (rec.player or {}).get("name")),
                    str((rec.player or {}).get("team")), market, str(outcome.get("name", "")).upper(),
                    line, american, decimal, 1 / decimal, str(book.get("key") or book.get("title")),
                    snapshot_timestamp, str(raw_market.get("last_update") or book.get("last_update") or snapshot_timestamp),
                    snapshot_timestamp, source, str(event.get("id") or event.get("event_id")),
                ).to_dict()
                row.update({"provider_market":raw_market.get("key"),"provider_player_name":outcome.get("description"),
                    "player_id":row["canonical_player_id"],"canonical_player_name":row["player_name"],
                    "side":row["selection"],"sportsbook":row["bookmaker"],"provider":"the-odds-api",
                    "requested_snapshot_timestamp":requested_snapshot_timestamp or snapshot_timestamp,
                    "provider_snapshot_timestamp":snapshot_timestamp,"captured_at":captured_at or snapshot_timestamp,
                    "provider_player_id":first_player_id(outcome.get("player_id"), outcome.get("athlete_id"), outcome.get("provider_player_id")),
                    "reconciliation_method":rec.status,"reconciliation_status":rec.status,
                    "reconciliation_candidate_count":rec.candidate_count,"reconciliation_confidence":"exact",
                    "identity_source":(rec.player or {}).get("source"),
                    "identity_provenance":(rec.player or {}).get("identity_provenance",[]),
                    "identity_has_stats":bool((rec.player or {}).get("has_stats"))})
                rows.append(row)
    return rows, rejected


# One quote is one side at one exact line, book, and provider snapshot.  Price is
# deliberately not in the identity: two source copies with different prices are
# a conflict for the same quote, not two independently actionable quotes.
CANONICAL_QUOTE_IDENTITY_FIELDS = ("league", "season", "week", "game_id",
    "canonical_player_id", "market", "bookmaker", "line", "selection",
    "provider_snapshot_timestamp")


def canonical_quote_identity(row: dict[str, Any]) -> tuple[Any, ...]:
    values=[]
    for field in CANONICAL_QUOTE_IDENTITY_FIELDS:
        if field == "selection": values.append(str(row.get("selection") or row.get("side") or "").upper())
        elif field == "provider_snapshot_timestamp": values.append(row.get(field) or row.get("snapshot_timestamp"))
        elif field == "canonical_player_id":
            canonical=first_player_id(row.get(field), row.get("player_id"))
            # Diagnostic/rejected rows must not make unrelated players collide
            # merely because final canonical reconciliation is unavailable.
            values.append(canonical or "provider:"+_name(row.get("provider_player_id") or row.get("provider_player_name") or row.get("description") or row.get("player_name")))
        else: values.append(row.get(field))
    return tuple(values)


def deduplicate_quotes(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collapse source duplicates with deterministic, timestamp-aware precedence."""
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows: groups.setdefault(canonical_quote_identity(row), []).append(row)
    result=[]; exact=conflicts=0; conflict_details=[]
    for identity, copies in groups.items():
        serialized=[json.dumps(r,sort_keys=True,separators=(",",":")) for r in copies]
        unique={value for value in serialized}
        if len(copies)>1:
            if len(unique)==1: exact += len(copies)-1
            else:
                conflicts += len(unique)-1
                conflict_details.append({"identity":list(identity),"copies":len(copies),
                                         "prices":sorted({r.get("american_odds") for r in copies},key=repr)})
        # Prefer the most recently updated source value; JSON is a stable
        # tiebreaker when the provider supplies contradictory copies at once.
        chosen=max(copies,key=lambda r:(parse_dt(r.get("market_last_update")) or parse_dt("1970-01-01T00:00:00Z"),
                                        json.dumps(r,sort_keys=True,separators=(",",":"))))
        result.append(chosen)
    result.sort(key=canonical_quote_identity)
    return result,{"duplicate_exact":exact,"duplicate_conflict":conflicts,"conflicts":conflict_details}


def filter_player_quotes(game: dict[str, Any], quotes: list[dict[str, Any]]):
    """Use the shared team-market cutoff semantics, including all timestamps."""
    relevant = [q for q in quotes if str(q.get("game_id")) == str(game.get("game_id"))]
    return filter_market_quotes(game, relevant)


def quote_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(k) for k in ("game_id", "canonical_player_id", "market", "bookmaker", "line", "snapshot_timestamp"))


def validate_player_prop_rows(rows: Iterable[dict[str, Any]], games: Iterable[dict[str, Any]],
                              players: Iterable[dict[str, Any]]) -> list[str]:
    """Validate the dedicated dataset independently and return every failure."""
    games_by_id={str(g.get("game_id")):g for g in games}; player_keys={(str(p.get("game_id")),first_player_id(p.get("player_id"),p.get("canonical_player_id"),p.get("athlete_id"))) for p in players}
    errors=[]; seen=set()
    for index,row in enumerate(rows):
        prefix=f"row[{index}]"
        game=games_by_id.get(str(row.get("game_id")))
        if not game: errors.append(f"{prefix}: canonical_game_match")
        canonical_id=first_player_id(row.get("canonical_player_id"),row.get("player_id"))
        if canonical_id is None: errors.append(f"{prefix}: canonical_player_id_required")
        elif (str(row.get("game_id")),canonical_id) not in player_keys and not canonical_id.startswith("history:"): errors.append(f"{prefix}: canonical_player_match")
        if row.get("market") not in CANONICAL_PLAYER_PROP_MARKETS: errors.append(f"{prefix}: supported_market")
        for field in ("line","american_odds"):
            if isinstance(row.get(field),bool) or not isinstance(row.get(field),(int,float)): errors.append(f"{prefix}: numeric_{field}")
        if str(row.get("selection") or row.get("side")).upper() not in {"OVER","UNDER"}: errors.append(f"{prefix}: valid_side")
        snap=parse_dt(row.get("provider_snapshot_timestamp") or row.get("snapshot_timestamp")); cutoff=prediction_cutoff(game) if game else None
        update=parse_dt(row.get("market_last_update")) if row.get("market_last_update") else None
        if not snap: errors.append(f"{prefix}: invalid_provider_snapshot")
        elif cutoff and snap > cutoff: errors.append(f"{prefix}: provider_snapshot_after_cutoff")
        if row.get("market_last_update") and not update: errors.append(f"{prefix}: invalid_market_last_update")
        # Historical envelope timestamps identify the provider's selected
        # archive record.  Bookmaker last_update is leakage-safe when it is no
        # later than the requested as-of boundary (and the game cutoff).
        requested=parse_dt(row.get("requested_snapshot_timestamp"))
        if requested and snap and snap > requested: errors.append(f"{prefix}: provider_snapshot_after_requested_snapshot")
        if update and requested and update > requested: errors.append(f"{prefix}: market_update_after_requested_snapshot")
        identity=canonical_quote_identity(row)
        if identity in seen: errors.append(f"{prefix}: duplicate_canonical_quote_identity")
        seen.add(identity)
        if row.get("reconciliation_status") not in {None,"matched",EXACT_PROVIDER_ID,EXACT_NAME_TEAM_GAME,EXACT_NAME_TEAM}: errors.append(f"{prefix}: deterministic_player_reconciliation")
    return errors


def pair_quotes(quotes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
    for q in quotes: grouped.setdefault(quote_key(q), {})[str(q.get("selection", "")).upper()] = q
    pairs=[]
    for key, sides in grouped.items():
        over, under = sides.get("OVER"), sides.get("UNDER")
        implied = {s: q.get("implied_probability") or 1 / float(q["decimal_odds"]) for s,q in sides.items()}
        total = sum(implied.values()) if over and under else None
        pairs.append({"key": key, "over": over, "under": under, "complete": bool(over and under),
                      "no_vig_over": implied.get("OVER") / total if total else None,
                      "no_vig_under": implied.get("UNDER") / total if total else None})
    return pairs


def execution_and_consensus(quotes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compute consensus separately at each exact line; never average lines."""
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for pair in pair_quotes(quotes):
        base = pair["over"] or pair["under"]
        key = tuple(base.get(k) for k in ("game_id", "canonical_player_id", "market", "line", "snapshot_timestamp"))
        buckets.setdefault(key, []).append(pair)
    result=[]
    for key, pairs in buckets.items():
        complete=[p for p in pairs if p["complete"]]
        all_quotes=[p[s] for p in pairs for s in ("over", "under") if p[s]]
        best={side: max((q for q in all_quotes if q["selection"] == side), key=lambda q: q["decimal_odds"], default=None) for side in ("OVER","UNDER")}
        result.append({"key": key, "consensus_line": key[3], "books": len(pairs),
                       "consensus_no_vig_over": sum(p["no_vig_over"] for p in complete)/len(complete) if complete else None,
                       "best_over": best["OVER"], "best_under": best["UNDER"]})
    return result


def grade_quote(quote: dict[str, Any], outcomes: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Grade only the canonical game/player/market triple."""
    matches=[r for r in outcomes if str(r.get("game_id")) == str(quote.get("game_id")) and
             str(r.get("canonical_player_id") or r.get("player_id")) == str(quote.get("canonical_player_id"))]
    if len(matches) != 1: raise ValueError("exactly one canonical player outcome is required")
    actual=(matches[0].get("stats") or {}).get(quote["market"], matches[0].get(quote["market"]))
    if actual is None: raise ValueError("outcome market is missing")
    line=float(quote["line"]); actual=float(actual)
    result="push" if actual == line else ("win" if (actual > line) == (quote["selection"] == "OVER") else "loss")
    return {**quote, "actual_stat": actual, "result": result,
            "over_result": "push" if actual == line else "win" if actual > line else "loss",
            "under_result": "push" if actual == line else "win" if actual < line else "loss"}


def simulation_fair_sgp_price(joint_probability: float) -> dict[str, Any]:
    if not 0 < joint_probability <= 1: raise ValueError("joint_probability must be in (0, 1]")
    decimal = 1 / joint_probability
    return {"price_type": "MODEL_FAIR_PRICE_NOT_SPORTSBOOK_QUOTE", "joint_probability": joint_probability,
            "simulation_fair_decimal_odds": decimal, "simulation_fair_american_odds": american_from_decimal(decimal),
            "sportsbook_sgp_price": None, "sportsbook_ev": None}


def availability(quotes: Iterable[dict[str, Any]], *, requested_weeks: Iterable[int]) -> dict[str, Any]:
    rows=list(quotes); weeks=set(requested_weeks); result={}
    for market in CANONICAL_PLAYER_PROP_MARKETS:
        found=[q for q in rows if q.get("market") == market]; covered={int(q["week"]) for q in found}
        status="READY" if weeks and covered >= weeks else "PARTIAL" if covered else "NOT_READY"
        result[market]={"FEATURE_HISTORY_READY":"READY", "OUTCOME_GRADING_READY":"READY",
                        "HISTORICAL_LINE_READY":status, "HISTORICAL_PRICE_READY":status,
                        "weeks":sorted(covered), "bookmakers":sorted({q["bookmaker"] for q in found})}
    result["HISTORICAL_SGP_BOOK_PRICE_READY"]="NOT_READY"
    return result


def evaluate_persisted_quotes(quotes: Iterable[dict[str, Any]], outcomes: Iterable[dict[str, Any]],
                              simulation_probabilities: dict[tuple[Any, ...], float]) -> dict[str, Any]:
    """Join persisted book prices to existing simulation probabilities offline.

    The caller supplies already-computed probabilities; this plumbing does not
    alter simulation formulas. Keys are ``(game_id, player_id, market, line,
    side)``. Missing model probabilities or outcomes remain explicit exclusions.
    """
    pairs={p["key"]:p for p in pair_quotes(quotes)}; evaluated=[]; exclusions=[]
    for quote in quotes:
        side=str(quote.get("selection") or quote.get("side")).upper()
        key=(str(quote.get("game_id")),str(quote.get("canonical_player_id")),quote.get("market"),quote.get("line"),side)
        probability=simulation_probabilities.get(key)
        if probability is None:
            exclusions.append({"reason":"simulation_probability_missing","key":key}); continue
        try: graded=grade_quote(quote,outcomes)
        except ValueError as exc:
            exclusions.append({"reason":"outcome_missing","key":key,"detail":str(exc)}); continue
        pair=pairs.get(quote_key(quote)); no_vig=(pair or {}).get("no_vig_over" if side=="OVER" else "no_vig_under")
        implied=quote.get("implied_probability")
        if implied is None: implied=1/float(quote["decimal_odds"])
        evaluated.append({**graded,"sportsbook_implied_probability":float(implied),"no_vig_probability":no_vig,
                          "simulation_probability":float(probability),"model_edge":float(probability)-float(implied),
                          "grade":str(graded["result"]).upper(),"edge_bucket":_bucket(float(probability)-float(implied),.05),
                          "probability_bucket":_bucket(float(probability),.1)})
    dimensions={name:_counts(evaluated,name) for name in ("market","canonical_player_id","bookmaker","edge_bucket","probability_bucket","week")}
    return {"evaluated_quotes":evaluated,"quote_count":len(evaluated),"exclusions":exclusions,"diagnostics":dimensions,
            "price_type":"INDIVIDUAL_PLAYER_PROP","sgp_price_type":"MODEL_FAIR_SGP","historical_sgp_book_price_ready":False}


def _bucket(value: float, width: float) -> str:
    low=int(value/width) * width
    return f"{low:.2f}..{low+width:.2f}"


def _counts(rows: Iterable[dict[str, Any]], field: str) -> dict[str, int]:
    result={}
    for row in rows:
        key=str(row.get(field)); result[key]=result.get(key,0)+1
    return dict(sorted(result.items()))
