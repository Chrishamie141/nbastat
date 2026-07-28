"""Deterministic, model-agnostic NFL portfolio and ticket construction.

This module deliberately knows nothing about how a football projection is made.
It consumes frozen pre-kickoff candidate records and owns selection, exposure,
ticket probability, and settlement concerns.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from enum import Enum
from hashlib import sha256
import math
from typing import Any, Iterable, Protocol

from .grader import PredictionGrader
from .markets import normalize_market


class RiskProfile(str, Enum):
    SAFE = "safe"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


class TicketType(str, Enum):
    SINGLE = "single"
    WINNER_PARLAY = "winner_parlay"
    SAME_GAME_PARLAY = "same_game_parlay"
    SLATE_PARLAY = "slate_parlay"


@dataclass(frozen=True)
class BetPolicy:
    """All strategy thresholds in one auditable, replaceable policy."""

    risk_profile: RiskProfile
    minimum_model_probability: float
    minimum_edge: float
    minimum_ev: float
    minimum_confidence_score: float
    minimum_value_score: float
    minimum_legs: int
    maximum_legs: int
    maximum_legs_per_game: int
    maximum_team_exposure: int
    allow_underdogs: bool
    require_diversification: bool


# Conservative starting rules chosen a priori, not fitted to historical weeks.
DEFAULT_POLICIES = {
    RiskProfile.SAFE: BetPolicy(RiskProfile.SAFE, .58, .015, .01, .62, .40, 2, 3, 1, 1, False, True),
    RiskProfile.BALANCED: BetPolicy(RiskProfile.BALANCED, .52, .02, .015, .54, .48, 2, 4, 1, 1, True, True),
    RiskProfile.AGGRESSIVE: BetPolicy(RiskProfile.AGGRESSIVE, .40, .025, .02, .44, .56, 3, 6, 1, 2, True, True),
}


def american_to_decimal(odds: float) -> float:
    odds = float(odds)
    if odds == 0: raise ValueError("American odds cannot be zero")
    return 1 + (100 / abs(odds) if odds < 0 else odds / 100)


def decimal_to_american(odds: float) -> int:
    odds = float(odds)
    if odds <= 1: raise ValueError("Decimal odds must exceed one")
    value = -100 / (odds - 1) if odds < 2 else (odds - 1) * 100
    return int(round(value))


@dataclass(frozen=True)
class BetCandidate:
    league: str; season: str; week: int; game_id: str; kickoff_time: str
    home_team: str; away_team: str; model_name: str; market: str; selection: str
    line: float | None; american_odds: float; decimal_odds: float
    implied_probability: float; model_probability: float; edge: float
    expected_value: float; bookmaker: str; snapshot_timestamp: str; data_as_of: str
    grading_identity: str; final_result: str | None = None
    confidence_score: float = 0.; value_score: float = 0.
    calibration_score: float | None = None; model_agreement: float | None = None
    market_quality: float = 1.; data_completeness: float = 1.
    reasons: tuple[str, ...] = ()

    @property
    def candidate_id(self) -> str:
        raw = "|".join(map(str, (self.league, self.season, self.week, self.game_id,
                                  self.model_name, self.market, self.selection, self.line,
                                  self.bookmaker, self.american_odds)))
        return sha256(raw.encode()).hexdigest()[:20]

    @property
    def teams_exposed(self) -> tuple[str, ...]:
        if self.market == "total": return tuple(sorted((self.home_team, self.away_team)))
        selected = canonical_selection(self)
        return (selected,) if selected else tuple(sorted((self.home_team, self.away_team)))


def normalize_candidate(row: dict[str, Any]) -> BetCandidate:
    """Normalize a replay row without altering its authoritative probability/edge."""
    american = float(row.get("american_odds", row.get("sportsbook_odds", row.get("odds"))))
    decimal = float(row.get("decimal_odds") or american_to_decimal(american))
    implied = float(row.get("implied_probability") or 1 / decimal)
    probability = float(row["model_probability"])
    edge = float(row.get("edge", probability - implied))
    ev = float(row.get("expected_value", probability * decimal - 1))
    completeness_fields = ("game_id", "kickoff_time", "home_team", "away_team",
                           "snapshot_timestamp", "data_as_of", "bookmaker")
    completeness = float(row.get("data_completeness", sum(bool(row.get(k) or (k == "bookmaker" and row.get("sportsbook"))) for k in completeness_fields) / len(completeness_fields)))
    calibration = row.get("calibration_score")
    agreement = row.get("model_agreement")
    # Confidence is outcome likelihood/quality. Value is price discrepancy/EV.
    confidence = .70 * probability + .10 * float(row.get("market_quality", 1)) + .10 * completeness + .10 * float(calibration if calibration is not None else .5)
    value = .55 * max(0., min(1., edge / .15)) + .35 * max(0., min(1., ev / .30)) + .10 * float(agreement if agreement is not None else .5)
    market = normalize_market(row.get("market"))
    game_id = str(row.get("game_id") or row.get("game") or "")
    selection = str(row.get("selection", row.get("prediction", "")))
    identity = str(row.get("grading_identity") or f"{game_id}:{market}:{selection}:{row.get('line')}")
    return BetCandidate(
        league=str(row.get("league", "nfl")).lower(), season=str(row.get("season", "")), week=int(row.get("week", 0)),
        game_id=game_id, kickoff_time=str(row.get("kickoff_time") or row.get("commence_time") or ""),
        home_team=str(row.get("home_team", "")), away_team=str(row.get("away_team", "")), model_name=str(row.get("model_name") or row.get("prediction_model_version") or row.get("model") or ""),
        market=market, selection=selection, line=float(row["line"]) if row.get("line") not in (None, "") else None,
        american_odds=american, decimal_odds=decimal, implied_probability=implied, model_probability=probability,
        edge=edge, expected_value=ev, bookmaker=str(row.get("bookmaker") or row.get("sportsbook") or "unknown"),
        snapshot_timestamp=str(row.get("snapshot_timestamp") or row.get("captured_at") or ""),
        data_as_of=str(row.get("data_as_of") or row.get("features_data_as_of") or row.get("captured_at") or ""),
        grading_identity=identity, final_result=row.get("final_result") or row.get("grade"),
        confidence_score=round(confidence, 12), value_score=round(value, 12),
        calibration_score=float(calibration) if calibration is not None else None,
        model_agreement=float(agreement) if agreement is not None else None,
        market_quality=float(row.get("market_quality", 1)), data_completeness=completeness,
    )


@dataclass
class Ticket:
    ticket_id: str; ticket_type: str; risk_profile: str; model_name: str
    created_from_snapshot: str; legs: list[BetCandidate]; number_of_legs: int
    combined_decimal_odds: float; combined_american_odds: int | None
    estimated_joint_probability: float | None; estimated_fair_odds: float | None
    estimated_ticket_ev: float | None; confidence_diagnostics: dict[str, Any]
    correlation_status: str; exposure_diagnostics: dict[str, Any]
    construction_reasons: list[str]; rejection_reasons: list[str] = field(default_factory=list)
    warning_reasons: list[str] = field(default_factory=list); historical_grade: str | None = None
    stake: float = 0.; payout: float | None = None; profit: float | None = None

    def as_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class Rejection:
    candidate_id: str | None; reason: str; detail: str = ""


@dataclass
class BuildResult:
    tickets: list[Ticket] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)

    @property
    def no_bet(self) -> bool: return not self.tickets


class JointProbabilityEstimator(Protocol):
    def estimate(self, legs: list[BetCandidate]) -> tuple[float | None, str]: ...


class ConservativeJointProbabilityEstimator:
    def estimate(self, legs: list[BetCandidate]) -> tuple[float | None, str]:
        if len({leg.game_id for leg in legs}) != len(legs):
            return None, "correlated_probability_unavailable"
        return math.prod(leg.model_probability for leg in legs), "independence_assumption"


def canonical_selection(candidate: BetCandidate) -> str | None:
    value = candidate.selection.casefold()
    if value == "home": return candidate.home_team
    if value == "away": return candidate.away_team
    for team in (candidate.home_team, candidate.away_team):
        if value == team.casefold(): return team
    return None


def conflict_reason(left: BetCandidate, right: BetCandidate) -> str | None:
    if left.candidate_id == right.candidate_id or left.grading_identity == right.grading_identity:
        return "duplicate_leg"
    if left.game_id != right.game_id: return None
    if left.market == right.market == "h2h" and canonical_selection(left) != canonical_selection(right): return "conflicting_selection"
    if left.market == right.market == "total" and left.line == right.line and left.selection.casefold() != right.selection.casefold(): return "conflicting_selection"
    if left.market == right.market == "spread" and canonical_selection(left) != canonical_selection(right): return "conflicting_selection"
    return None


def eligibility_reasons(candidate: BetCandidate, policy: BetPolicy) -> list[str]:
    reasons = []
    if candidate.model_probability < policy.minimum_model_probability: reasons.append("insufficient_probability")
    if candidate.edge < policy.minimum_edge: reasons.append("insufficient_edge")
    if candidate.expected_value < policy.minimum_ev: reasons.append("insufficient_ev")
    if candidate.confidence_score < policy.minimum_confidence_score: reasons.append("insufficient_confidence")
    if candidate.value_score < policy.minimum_value_score: reasons.append("insufficient_value")
    if not policy.allow_underdogs and candidate.implied_probability < .5: reasons.append("underdog_not_allowed")
    return reasons


def _rank(c: BetCandidate, profile: RiskProfile) -> tuple[Any, ...]:
    score = (.70*c.confidence_score + .30*c.value_score if profile is RiskProfile.SAFE else
             .50*c.confidence_score + .50*c.value_score if profile is RiskProfile.BALANCED else
             .30*c.confidence_score + .70*c.value_score)
    return (-score, -c.model_probability, -c.expected_value, c.game_id, c.market, c.selection, c.candidate_id)


def _make_ticket(ticket_type: TicketType, profile: RiskProfile, legs: list[BetCandidate], stake: float,
                 estimator: JointProbabilityEstimator, reasons: list[str]) -> Ticket:
    if len({leg.model_name for leg in legs}) != 1: raise ValueError("mixed_model_ticket")
    decimal = math.prod(leg.decimal_odds for leg in legs)
    joint, correlation = estimator.estimate(legs)
    fair = 1 / joint if joint else None
    ev = joint * decimal - 1 if joint is not None else None
    raw = "|".join([ticket_type.value, profile.value, *(leg.candidate_id for leg in legs)])
    games = Counter(leg.game_id for leg in legs); teams = Counter(t for leg in legs for t in leg.teams_exposed)
    warnings = ["correlation_probability_unavailable"] if joint is None else []
    return Ticket(sha256(raw.encode()).hexdigest()[:24], ticket_type.value, profile.value, legs[0].model_name,
                  min((leg.snapshot_timestamp for leg in legs), default=""), legs, len(legs), decimal,
                  decimal_to_american(decimal), joint, fair, ev,
                  {"average_confidence": sum(x.confidence_score for x in legs)/len(legs), "average_value": sum(x.value_score for x in legs)/len(legs)},
                  correlation, {"games": dict(games), "teams": dict(teams)}, reasons, warning_reasons=warnings, stake=stake)


class TicketEngine:
    def __init__(self, policies: dict[RiskProfile, BetPolicy] | None = None,
                 estimator: JointProbabilityEstimator | None = None):
        self.policies = policies or DEFAULT_POLICIES
        self.estimator = estimator or ConservativeJointProbabilityEstimator()

    def singles(self, candidates: Iterable[BetCandidate], profile: RiskProfile = RiskProfile.BALANCED, stake: float = 10) -> BuildResult:
        result = BuildResult(); policy = self.policies[profile]
        for candidate in sorted(candidates, key=lambda c: _rank(c, profile)):
            reasons = eligibility_reasons(candidate, policy)
            if reasons:
                result.rejections.extend(Rejection(candidate.candidate_id, reason) for reason in reasons); continue
            result.tickets.append(_make_ticket(TicketType.SINGLE, profile, [candidate], stake, self.estimator,
                                               ["qualified_probability", "qualified_value", "independent_single"]));
        if not result.tickets: result.rejections.append(Rejection(None, "insufficient_qualifying_legs"))
        return result

    def winner_parlay(self, candidates: Iterable[BetCandidate], profile: RiskProfile, stake: float = 10) -> BuildResult:
        return self._diversified(candidates, profile, TicketType.WINNER_PARLAY, stake, markets={"h2h"})

    def slate_parlay(self, candidates: Iterable[BetCandidate], profile: RiskProfile, stake: float = 10) -> BuildResult:
        return self._diversified(candidates, profile, TicketType.SLATE_PARLAY, stake, markets={"h2h", "spread", "total"})

    def _diversified(self, candidates: Iterable[BetCandidate], profile: RiskProfile, kind: TicketType, stake: float, markets: set[str]) -> BuildResult:
        policy=self.policies[profile]; result=BuildResult(); chosen=[]; game_counts=Counter(); team_counts=Counter()
        for candidate in sorted((c for c in candidates if c.market in markets), key=lambda c:_rank(c, profile)):
            reasons=eligibility_reasons(candidate, policy)
            if any(conflict_reason(candidate, existing) for existing in chosen): reasons.append(next(conflict_reason(candidate,x) for x in chosen if conflict_reason(candidate,x)))
            if game_counts[candidate.game_id] >= policy.maximum_legs_per_game: reasons.append("same_game_exposure_limit")
            if any(team_counts[t] >= policy.maximum_team_exposure for t in candidate.teams_exposed): reasons.append("same_team_exposure_limit")
            if reasons: result.rejections.extend(Rejection(candidate.candidate_id,r) for r in dict.fromkeys(reasons)); continue
            chosen.append(candidate); game_counts[candidate.game_id]+=1
            for team in candidate.teams_exposed: team_counts[team]+=1
            if len(chosen) == policy.maximum_legs: break
        if len(chosen) < policy.minimum_legs:
            result.rejections.append(Rejection(None,"insufficient_qualifying_legs")); return result
        result.tickets.append(_make_ticket(kind,profile,chosen,stake,self.estimator,["policy_thresholds_met","diversified_across_games"]))
        return result

    def same_game_parlays(self, candidates: Iterable[BetCandidate], profile: RiskProfile, stake: float = 10) -> BuildResult:
        """Construct game-script-aware tickets; never applies independent multiplication."""
        policy=self.policies[profile]; result=BuildResult(); grouped=defaultdict(list)
        for candidate in candidates: grouped[candidate.game_id].append(candidate)
        for game_id in sorted(grouped):
            eligible=[]
            for c in sorted(grouped[game_id],key=lambda x:_rank(x,profile)):
                reasons=eligibility_reasons(c,policy)
                if reasons: result.rejections.extend(Rejection(c.candidate_id,r) for r in reasons)
                else: eligible.append(c)
            pair=self._coherent_pair(eligible)
            if not pair: result.rejections.append(Rejection(None,"incoherent_game_script",game_id)); continue
            result.tickets.append(_make_ticket(TicketType.SAME_GAME_PARLAY,profile,pair,stake,self.estimator,["coherent_game_script","correlation_not_quantified"]))
        if not result.tickets: result.rejections.append(Rejection(None,"insufficient_qualifying_legs"))
        return result

    @staticmethod
    def _coherent_pair(candidates: list[BetCandidate]) -> list[BetCandidate] | None:
        # Supported initial scripts: favorite ML + over; underdog spread + under.
        for first in candidates:
            for second in candidates:
                if first is second or conflict_reason(first,second): continue
                legs={first.market: first, second.market: second}
                if set(legs)=={"h2h","total"}:
                    ml,total=legs["h2h"],legs["total"]
                    if ml.implied_probability >= .5 and total.selection.casefold()=="over": return [ml,total]
                if set(legs)=={"spread","total"}:
                    spread,total=legs["spread"],legs["total"]
                    if (spread.line or 0)>0 and total.selection.casefold()=="under": return [spread,total]
        return None


def grade_ticket(ticket: Ticket, outcomes: dict[str, dict[str, Any]]) -> Ticket:
    grader=PredictionGrader(); grades=[]
    for leg in ticket.legs:
        grades.append(grader.grade({"game_id":leg.game_id,"market":leg.market,"selection":leg.selection,"line":leg.line},outcomes.get(leg.game_id))["grade"])
    if "loss" in grades: grade="loss"
    elif "ungraded" in grades: grade="ungraded"
    elif all(g=="push" for g in grades): grade="push"
    else: grade="win"  # pushed legs are void and remaining prices stand
    effective=math.prod(leg.decimal_odds for leg,g in zip(ticket.legs,grades) if g!="push")
    ticket.historical_grade=grade
    ticket.payout = ticket.stake * effective if grade=="win" else ticket.stake if grade=="push" else 0 if grade=="loss" else None
    ticket.profit = ticket.payout-ticket.stake if ticket.payout is not None else None
    return ticket
