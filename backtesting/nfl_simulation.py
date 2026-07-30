"""Leakage-safe, deterministic NFL game and player Monte Carlo primitives.

The first score model is an over-dispersed Poisson mixture.  A shared gamma
game-environment draw induces score correlation and team-specific gamma draws
represent offensive/defensive uncertainty.  Scores are consequently integer,
non-negative, and not an independent-normal approximation.  This module is
offline-only: callers provide projections and timestamped history.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import sqrt
from typing import Any, Iterable, Protocol

import numpy as np

from .game_matching import normalize_team
from .team_history import history_known_at, prediction_cutoff

SIMULATION_VERSION = "nfl-game-simulation-v1"
PLAYER_MARKETS = ("passing_yards", "passing_tds", "rushing_attempts", "rushing_yards",
                  "receptions", "receiving_yards")


def _number(row: dict[str, Any], *keys: str) -> float:
    for key in keys:
        try:
            if row.get(key) is not None:
                return float(row[key])
        except (TypeError, ValueError):
            continue
    return 0.0


@dataclass(frozen=True)
class PlayerUsage:
    player_id: str
    player_name: str
    team: str
    position: str
    games_observed: int
    recent_participation_rate: float
    passing_attempt_share_proxy: float = 0.0
    rush_attempt_share_proxy: float = 0.0
    reception_share_proxy: float = 0.0
    receiving_yard_share_proxy: float = 0.0
    availability: str = "unknown"
    availability_confidence: float = 0.0
    source_data_as_of: str | None = None


class PlayerParticipationModel:
    """Creates explicitly named usage proxies from rows known before kickoff."""
    def build(self, game: dict[str, Any], rows: list[dict[str, Any]]) -> list[PlayerUsage]:
        cutoff = prediction_cutoff(game)
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            known = history_known_at(row)
            if not cutoff or not known or known >= cutoff or row.get("game_id") == game.get("game_id"):
                continue
            team = normalize_team(row.get("team")); player = str(row.get("player_id") or row.get("player") or row.get("player_name") or "")
            if team not in {normalize_team(game.get("home_team")), normalize_team(game.get("away_team"))} or not player:
                continue
            grouped.setdefault((team, player), []).append(row)
        result = []
        for (team, player), history in sorted(grouped.items()):
            history.sort(key=lambda r: str(r.get("data_as_of") or r.get("completed_at")))
            totals = {market: sum(_number(r, market) for r in history) for market in PLAYER_MARKETS}
            team_totals = {}
            for market in PLAYER_MARKETS:
                games: dict[str, float] = {}
                for other in rows:
                    known = history_known_at(other)
                    if known and cutoff and known < cutoff and normalize_team(other.get("team")) == team:
                        gid = str(other.get("game_id") or other.get("week") or known)
                        games[gid] = games.get(gid, 0.0) + _number(other, market)
                team_totals[market] = sum(games.values())
            # Beta-style shrinkage prevents one-game players receiving extreme shares.
            share = lambda key, prior: (totals[key] + 3 * prior) / (team_totals[key] + 3) if team_totals[key] + 3 else prior
            latest = history[-1]
            result.append(PlayerUsage(
                player_id=player, player_name=str(latest.get("player_name") or latest.get("player") or player),
                team=team, position=str(latest.get("position") or "UNKNOWN").upper(), games_observed=len(history),
                recent_participation_rate=min(1.0, len(history[-3:]) / 3),
                passing_attempt_share_proxy=min(1.0, share("passing_yards", .02)),
                rush_attempt_share_proxy=min(1.0, share("rushing_attempts", .08)),
                reception_share_proxy=min(1.0, share("receptions", .08)),
                receiving_yard_share_proxy=min(1.0, share("receiving_yards", .08)),
                availability=str(latest.get("availability") or "unknown"),
                availability_confidence=1.0 if latest.get("availability") is not None else 0.0,
                source_data_as_of=str(latest.get("data_as_of") or latest.get("completed_at"))))
        return result


class ScoreDistribution(Protocol):
    def sample(self, home_mean: float, away_mean: float, n: int, rng: np.random.Generator,
               history: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, dict[str, float]]: ...


class GammaPoissonScoreDistribution:
    """Correlated negative-binomial-like scores, preserving projection means."""
    def sample(self, home_mean: float, away_mean: float, n: int, rng: np.random.Generator,
               history: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
        observed = [_number(r, "points_for") for r in history if r.get("points_for") is not None]
        variance = float(np.var(observed)) if len(observed) > 3 else 100.0
        dispersion = max(8.0, min(40.0, ((home_mean + away_mean) / 2) ** 2 / max(1.0, variance - (home_mean + away_mean) / 2)))
        shared_shape = 18.0
        environment = rng.gamma(shared_shape, 1 / shared_shape, n)
        home_rate = home_mean * environment * rng.gamma(dispersion, 1 / dispersion, n)
        away_rate = away_mean * environment * rng.gamma(dispersion, 1 / dispersion, n)
        return rng.poisson(np.maximum(0, home_rate)), rng.poisson(np.maximum(0, away_rate)), {
            "method": "shared_gamma_poisson", "historical_variance": variance,
            "team_dispersion_shape": dispersion, "shared_environment_shape": shared_shape}


@dataclass(frozen=True)
class JointProbability:
    joint_probability: float
    sample_hits: int
    num_simulations: int
    standard_error: float


class SimulatedLeg(Protocol):
    def mask(self, result: "SimulationResult") -> np.ndarray: ...


@dataclass(frozen=True)
class Moneyline:
    team: str
    def mask(self, r: "SimulationResult") -> np.ndarray:
        return (r.home_points > r.away_points) if normalize_team(self.team) == normalize_team(r.home_team) else (r.away_points > r.home_points)


@dataclass(frozen=True)
class Spread:
    team: str
    line: float
    def mask(self, r: "SimulationResult") -> np.ndarray:
        margin = r.home_points-r.away_points if normalize_team(self.team) == normalize_team(r.home_team) else r.away_points-r.home_points
        return margin + self.line > 0


@dataclass(frozen=True)
class GameTotal:
    over: float | None = None
    under: float | None = None
    def mask(self, r: "SimulationResult") -> np.ndarray:
        line, is_over = (self.over, True) if self.over is not None else (self.under, False)
        if line is None: raise ValueError("over or under line is required")
        return r.game_total > line if is_over else r.game_total < line


@dataclass(frozen=True)
class PlayerProp:
    player: str
    market: str
    over: float | None = None
    under: float | None = None
    def mask(self, r: "SimulationResult") -> np.ndarray:
        values = r.player_outcomes[(self.player, self.market)]
        line, is_over = (self.over, True) if self.over is not None else (self.under, False)
        if line is None: raise ValueError("over or under line is required")
        return values > line if is_over else values < line


class PlayerPassingYards(PlayerProp):
    def __init__(self, player: str, *, over: float | None = None, under: float | None = None): super().__init__(player, "passing_yards", over, under)
class PlayerPassingTDs(PlayerProp):
    def __init__(self, player: str, *, over: float | None = None, under: float | None = None): super().__init__(player, "passing_tds", over, under)
class PlayerRushingYards(PlayerProp):
    def __init__(self, player: str, *, over: float | None = None, under: float | None = None): super().__init__(player, "rushing_yards", over, under)
class PlayerReceptions(PlayerProp):
    def __init__(self, player: str, *, over: float | None = None, under: float | None = None): super().__init__(player, "receptions", over, under)
class PlayerReceivingYards(PlayerProp):
    def __init__(self, player: str, *, over: float | None = None, under: float | None = None): super().__init__(player, "receiving_yards", over, under)


@dataclass
class SimulationResult:
    home_team: str
    away_team: str
    home_points: np.ndarray
    away_points: np.ndarray
    metadata: dict[str, Any]
    player_outcomes: dict[tuple[str, str], np.ndarray] = field(default_factory=dict)
    assumptions: dict[str, Any] = field(default_factory=dict)
    @property
    def game_total(self): return self.home_points + self.away_points
    @property
    def score_margin(self): return self.home_points - self.away_points
    def probability_of(self, legs: Iterable[SimulatedLeg]) -> JointProbability:
        mask = np.ones(len(self.home_points), dtype=bool)
        for leg in legs: mask &= leg.mask(self)
        hits = int(mask.sum()); p = hits / len(mask)
        return JointProbability(p, hits, len(mask), sqrt(p * (1-p) / len(mask)))
    def market_probability(self, market: str, line: float | None = None) -> dict[str, float]:
        if market == "moneyline": values = self.score_margin
        elif market == "spread": values = self.score_margin + float(line or 0)
        elif market == "total": values = self.game_total - float(line)
        else: raise ValueError(f"unsupported team market: {market}")
        return {"home" if market != "total" else "over": float(np.mean(values > 0)),
                "away" if market != "total" else "under": float(np.mean(values < 0)),
                "push": float(np.mean(values == 0))}
    def player_distribution(self, player: str, market: str, line: float | None = None) -> dict[str, Any]:
        values = self.player_outcomes[(player, market)]
        out = {"expected_value": float(np.mean(values)), "median": float(np.median(values)),
               "standard_deviation": float(np.std(values)), "quantiles": {str(q): float(np.quantile(values, q)) for q in (.1,.25,.5,.75,.9)}}
        if line is not None: out.update(over_probability=float(np.mean(values > line)), under_probability=float(np.mean(values < line)), push_probability=float(np.mean(values == line)))
        return out
    def correlation(self, left: np.ndarray | tuple[str,str], right: np.ndarray | tuple[str,str]) -> dict[str, Any]:
        a = self.player_outcomes[left] if isinstance(left, tuple) else left; b = self.player_outcomes[right] if isinstance(right, tuple) else right
        value = float(np.corrcoef(a, b)[0,1]) if np.std(a) and np.std(b) else 0.0
        return {"pearson": value, "sample_count": len(a), "diagnostic_only": True}
    def to_dict(self, include_samples: int = 0) -> dict[str, Any]:
        summary = {"metadata": self.metadata, "teams": {"home": self.home_team, "away": self.away_team},
                   "score": {"home_mean": float(np.mean(self.home_points)), "away_mean": float(np.mean(self.away_points)),
                             "home_sd": float(np.std(self.home_points)), "away_sd": float(np.std(self.away_points))},
                   "assumptions": self.assumptions,
                   "players": {f"{p}|{m}": self.player_distribution(p,m) for p,m in sorted(self.player_outcomes)}}
        if include_samples: summary["sample"] = [{"home_points": int(h), "away_points": int(a)} for h,a in zip(self.home_points[:include_samples], self.away_points[:include_samples])]
        return summary


class NFLGameSimulator:
    def __init__(self, score_distribution: ScoreDistribution | None = None):
        self.score_distribution = score_distribution or GammaPoissonScoreDistribution()

    def simulate(self, game: dict[str, Any], pregame_team_history: list[dict[str, Any]],
                 pregame_player_history: list[dict[str, Any]], market_context: dict[str, Any] | None,
                 model_version: str, num_simulations: int, seed: int,
                 projection: Any | None = None) -> SimulationResult:
        if num_simulations <= 0: raise ValueError("num_simulations must be positive")
        kickoff = str(game.get("kickoff_time") or game.get("commence_time") or "")
        cutoff = prediction_cutoff(game)
        for row in pregame_team_history + pregame_player_history:
            known = history_known_at(row)
            if known and cutoff and known >= cutoff: raise ValueError("future history row violates simulation cutoff")
        home_mean = float(getattr(projection, "home_points", game.get("projected_home_points", 22.5)))
        away_mean = float(getattr(projection, "away_points", game.get("projected_away_points", 21.0)))
        rng = np.random.default_rng(seed)
        home, away, score_notes = self.score_distribution.sample(home_mean, away_mean, num_simulations, rng, pregame_team_history)
        usages = PlayerParticipationModel().build(game, pregame_player_history)
        outcomes: dict[tuple[str,str], np.ndarray] = {}
        assumptions: dict[str, Any] = {"score_distribution": score_notes, "projected_scores": {"home": home_mean, "away": away_mean},
                                        "player_usage": [asdict(u) for u in usages]}
        for team, points, opponent in ((normalize_team(game.get("home_team")), home, away), (normalize_team(game.get("away_team")), away, home)):
            players = [u for u in usages if u.team == team and u.availability != "unavailable"]
            # Shared script: trailing raises pass rate; leading raises rush volume.
            margin = points-opponent; plays = np.maximum(40, np.rint(rng.normal(64, 5, num_simulations))).astype(int)
            pass_rate = np.clip(.57 - .004*margin + rng.normal(0,.025,num_simulations), .35,.78)
            pass_attempts = np.rint(plays*pass_rate).astype(int); rush_attempts = plays-pass_attempts
            passing_yards = np.maximum(0, pass_attempts*rng.normal(6.8,.65,num_simulations) + points*1.8)
            rushing_yards = np.maximum(0, rush_attempts*rng.normal(4.2,.5,num_simulations))
            passing_tds = rng.binomial(np.maximum(points//7, 0).astype(int), np.clip(.62 + .05*(pass_rate-.57), 0, 1))
            def normalized(attr: str) -> dict[str,float]:
                raw = {u.player_name: max(0.001, getattr(u, attr)) for u in players}; total=sum(raw.values())
                return {p:v/total for p,v in raw.items()} if total else {}
            qb = normalized("passing_attempt_share_proxy"); rush = normalized("rush_attempt_share_proxy")
            rec = normalized("reception_share_proxy"); recyd = normalized("receiving_yard_share_proxy")
            for u in players:
                p=u.player_name
                if u.position == "QB":
                    outcomes[p,"passing_yards"] = passing_yards*qb.get(p,0)
                    outcomes[p,"passing_tds"] = rng.binomial(passing_tds, qb.get(p,0))
                if u.position in {"RB","QB","FB"}:
                    attempts=rng.binomial(rush_attempts, rush.get(p,0)); outcomes[p,"rushing_attempts"]=attempts
                    outcomes[p,"rushing_yards"]=attempts*np.maximum(0,rng.normal(4.3,.8,num_simulations))
                if u.position in {"WR","TE","RB","FB"}:
                    targets=rng.binomial(pass_attempts, rec.get(p,0)); receptions=rng.binomial(targets,.66)
                    outcomes[p,"receptions"]=receptions
                    # Shared team passing output creates the intended QB/receiver correlation.
                    outcomes[p,"receiving_yards"]=passing_yards*recyd.get(p,0)*rng.gamma(12,1/12,num_simulations)
        metadata = {"model_version": model_version, "simulation_version": SIMULATION_VERSION, "seed": seed,
                    "num_simulations": num_simulations, "features_data_as_of": max([str(r.get("data_as_of") or r.get("completed_at") or "") for r in pregame_team_history+pregame_player_history] or [""]),
                    "generated_at": kickoff, "market_context_provided": market_context is not None}
        return SimulationResult(str(game.get("home_team")), str(game.get("away_team")), home, away, metadata, outcomes, assumptions)


def grade_player_prop(market: str, line: float, selection: str, outcome: dict[str, Any]) -> str:
    if market not in PLAYER_MARKETS: raise ValueError(f"unsupported player market: {market}")
    if outcome.get(market) is None: raise ValueError(f"missing canonical outcome for {market}")
    actual=float(outcome[market]); side=selection.casefold()
    if actual == float(line): return "push"
    if side not in {"over","under"}: raise ValueError("selection must be over or under")
    return "win" if (actual > line) == (side == "over") else "loss"


def audit_player_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n=len(rows); markets={}
    for market in PLAYER_MARKETS:
        present=sum(row.get(market) is not None for row in rows)
        markets[market]={"player_game_rows": present, "missing": n-present, "missing_rate": (n-present)/n if n else 0,
                         "outcomes_gradeable": present > 0}
    return {"rows": n, "markets": markets,
            "identity_issues": sum(not (r.get("player_id") or r.get("player") or r.get("player_name")) or not r.get("team") for r in rows)}
