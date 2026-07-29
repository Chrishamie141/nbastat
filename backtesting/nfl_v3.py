"""Leakage-safe NFL V3 research primitives.

This module is deliberately independent from the frozen V1/V2 implementations.
It contains no provider calls and accepts only timestamped historical records.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import erf, exp, log, sqrt
from pathlib import Path
from statistics import pstdev
from typing import Any, Iterable

from .game_matching import normalize_team, parse_dt
from .nfl_game_predictor import GameProjection

V3_MODEL_VERSION = "nfl_game_baseline_v3"
FEATURE_SCHEMA_VERSION = "nfl-v3-features-1"
CALIBRATION_VERSION = "nfl-v3-calibration-1"


def stable_hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@dataclass(frozen=True)
class NFLResearchSplit:
    development_start_week: int = 1
    development_end_week: int = 6
    holdout_start_week: int = 7

    def __post_init__(self) -> None:
        if not (0 < self.development_start_week <= self.development_end_week < self.holdout_start_week):
            raise ValueError("research windows must be positive, chronological, and non-overlapping")

    def window(self, week: int) -> str:
        if self.development_start_week <= week <= self.development_end_week:
            return "development"
        if week >= self.holdout_start_week:
            return "holdout"
        return "predevelopment"

    def assert_tuning_weeks(self, weeks: Iterable[int]) -> None:
        bad = sorted({int(w) for w in weeks if self.window(int(w)) == "holdout"})
        if bad:
            raise ValueError(f"holdout outcomes cannot be used for tuning: weeks {bad}")


@dataclass(frozen=True)
class NFLV3Config:
    version: str = "nfl-v3-config-1"
    recency_decay: float = .90
    recent_form_window: int = 5
    elo_k: float = 20.0
    offseason_regression: float = .33
    elo_home_advantage: float = 48.0
    home_field_points: float = 1.5
    offense_defense_blend: float = .5
    elo_probability_weight: float = .30
    market_blend_weight: float = .10
    margin_variance_floor: float = 10.0
    total_variance_floor: float = 12.0
    calibration_method: str = "platt"
    disabled_feature_groups: tuple[str, ...] = ()

    @property
    def configuration_hash(self) -> str:
        return stable_hash(asdict(self))


@dataclass(frozen=True)
class FeatureValue:
    value: Any
    source_timestamps: tuple[str, ...]
    provenance: str
    available: bool = True
    feature_missing: bool = False
    fallback_reason: str | None = None


@dataclass
class FeatureSnapshot:
    cutoff: str
    groups: dict[str, dict[str, FeatureValue]] = field(default_factory=dict)

    def add(self, group: str, name: str, feature: FeatureValue) -> None:
        cutoff = parse_dt(self.cutoff)
        if any(parse_dt(ts) is None or parse_dt(ts) >= cutoff for ts in feature.source_timestamps):
            raise ValueError(f"feature_timestamp must be strictly before prediction_cutoff: {group}.{name}")
        self.groups.setdefault(group, {})[name] = feature

    def values(self) -> dict[str, Any]:
        return {f"{group}.{name}": item.value for group, features in self.groups.items()
                for name, item in features.items()}

    def diagnostics(self) -> dict[str, Any]:
        return {group: {name: asdict(item) for name, item in values.items()} for group, values in self.groups.items()}


OPTIONAL_GROUPS = ("injuries", "weather", "starting_qb", "offensive_line", "travel", "coaching", "player_availability")


class NFLV3FeatureBuilder:
    """Build auditable features from completed records known before kickoff."""
    def __init__(self, config: NFLV3Config | None = None): self.config = config or NFLV3Config()

    def _rows(self, game: dict[str, Any], history: list[dict[str, Any]], team: str) -> list[dict[str, Any]]:
        cutoff = parse_dt(game.get("kickoff_time") or game.get("commence_time"))
        rows = []
        for row in history:
            known = parse_dt(row.get("data_as_of") or row.get("captured_at")); completed = parse_dt(row.get("completed_at"))
            if (normalize_team(row.get("team")) == team and known and completed and cutoff and known < cutoff
                    and completed < cutoff and known >= completed and row.get("game_id") != game.get("game_id")
                    and row.get("points_for") is not None and row.get("points_against") is not None): rows.append(row)
        return sorted(rows, key=lambda r: (parse_dt(r["completed_at"]), str(r.get("game_id"))))

    def build(self, game: dict[str, Any], history: list[dict[str, Any]], market: dict[str, Any] | None = None) -> FeatureSnapshot:
        cutoff = str(game.get("kickoff_time") or game.get("commence_time")); snap = FeatureSnapshot(cutoff)
        teams = {"home": normalize_team(game.get("home_team")), "away": normalize_team(game.get("away_team"))}
        for side, team in teams.items():
            rows = self._rows(game, history, team); stamps = tuple(str(r.get("data_as_of") or r.get("completed_at")) for r in rows)
            pf = [float(r["points_for"]) for r in rows]; pa = [float(r["points_against"]) for r in rows]
            for group, name, values, fallback in (("offense", f"{side}_points", pf, 22.5), ("defense", f"{side}_allowed", pa, 22.5)):
                missing = not values; value = sum(values)/len(values) if values else fallback
                snap.add(group, name, FeatureValue(value, stamps, "completed_game_history", not missing, missing, "league_baseline" if missing else None))
            recent = pf[-self.config.recent_form_window:]
            snap.add("recent_form", f"{side}_points", FeatureValue(sum(recent)/len(recent) if recent else 22.5, stamps[-len(recent):] if recent else (), "rolling_completed_games", bool(recent), not recent, "league_baseline" if not recent else None))
            sd = pstdev(pf) if len(pf)>1 else self.config.total_variance_floor/sqrt(2)
            snap.add("scoring_variance", f"{side}_sd", FeatureValue(sd, stamps, "completed_game_history", len(pf)>1, len(pf)<=1, "variance_floor" if len(pf)<=1 else None))
            last = parse_dt(rows[-1]["completed_at"]) if rows else None; start = parse_dt(cutoff)
            days = (start-last).total_seconds()/86400 if last and start else 7.0
            snap.add("rest", f"{side}_days", FeatureValue(days, stamps[-1:] if rows else (), "schedule_history", bool(rows), not rows, "neutral_seven_days" if not rows else None))
        market = market or {}
        for name in ("moneyline_probability", "spread", "total", "dispersion"):
            val = market.get(name); stamp = market.get("captured_at")
            valid_stamp = stamp and parse_dt(stamp) and parse_dt(stamp) < parse_dt(cutoff)
            snap.add("market_context", name, FeatureValue(float(val) if val is not None and valid_stamp else None,
                (str(stamp),) if valid_stamp else (), "historical_sportsbook_consensus", val is not None and bool(valid_stamp),
                val is None or not valid_stamp, "unavailable_pregame_market" if val is None else "market_not_before_cutoff" if not valid_stamp else None))
        for group in OPTIONAL_GROUPS:
            snap.add(group, "available", FeatureValue(False, (), "optional_future_interface", False, True, "historical_source_unavailable"))
        return snap


class ProbabilityCalibrator:
    """Small deterministic Platt/isotonic calibrator; fitting is explicitly window-guarded."""
    def __init__(self, method: str = "platt"): self.method, self.params, self.sample_size = method, None, 0
    def fit(self, observations: list[tuple[float, int, int]], split: NFLResearchSplit) -> "ProbabilityCalibrator":
        split.assert_tuning_weeks(w for _,_,w in observations); self.sample_size = len(observations)
        if not observations: self.params = (1.0, 0.0); return self
        if self.method == "platt":
            a,b=1.0,0.0
            for _ in range(200):
                ga=gb=0.0
                for p,y,_ in observations:
                    x=log(min(.999999,max(.000001,p))/(1-min(.999999,max(.000001,p)))); q=1/(1+exp(-(a*x+b)))
                    ga+=(q-y)*x; gb+=q-y
                a-=.05*ga/len(observations); b-=.05*gb/len(observations)
            self.params=(a,b)
        elif self.method == "isotonic":
            self.params=tuple(sorted((p,y) for p,y,_ in observations))
        else: raise ValueError("calibration method must be platt or isotonic")
        return self
    def predict(self, probability: float) -> float:
        if not self.params: return probability
        if self.method == "platt":
            a,b=self.params; x=log(min(.999999,max(.000001,probability))/(1-min(.999999,max(.000001,probability))))
            return 1/(1+exp(-(a*x+b)))
        near=min(self.params,key=lambda item: abs(item[0]-probability)); return float(near[1])


def chronological_folds(weeks: Iterable[int]) -> list[tuple[list[int], list[int]]]:
    ordered=sorted(set(int(w) for w in weeks)); return [(ordered[:i], [ordered[i]]) for i in range(1,len(ordered))]


class NFLGameMarketPredictorV3:
    def __init__(self, config: NFLV3Config | None = None):
        self.config=config or NFLV3Config(); self.builder=NFLV3FeatureBuilder(self.config); self.last_feature_diagnostics={}
    def project(self, game: dict[str, Any], histories: list[dict[str, Any]], market: dict[str, Any] | None = None) -> GameProjection | None:
        snapshot=self.builder.build(game,histories,market); v=snapshot.values()
        if not any(not item.feature_missing for group in snapshot.groups.values() for item in group.values() if item.provenance=="completed_game_history"): return None
        home_off=v["offense.home_points"]; away_off=v["offense.away_points"]; home_def=v["defense.home_allowed"]; away_def=v["defense.away_allowed"]
        w=self.config.offense_defense_blend
        hp=w*home_off+(1-w)*away_def+self.config.home_field_points/2
        ap=w*away_off+(1-w)*home_def-self.config.home_field_points/2
        margin=hp-ap; margin_sd=max(self.config.margin_variance_floor,sqrt(v["scoring_variance.home_sd"]**2+v["scoring_variance.away_sd"]**2))
        football=.5*(1+erf(margin/(margin_sd*sqrt(2))))
        market_p=v["market_context.moneyline_probability"]
        blended=football if market_p is None or "market_context" in self.config.disabled_feature_groups else (1-self.config.market_blend_weight)*football+self.config.market_blend_weight*market_p
        features={**v,"football_probability":football,"market_probability":market_p,"blended_probability":blended,"configuration_hash":self.config.configuration_hash}
        self.last_feature_diagnostics=snapshot.diagnostics()
        stamps=[ts for group in snapshot.groups.values() for item in group.values() for ts in item.source_timestamps]
        return GameProjection(hp,ap,margin,hp+ap,0.0,max(stamps) if stamps else None,features,V3_MODEL_VERSION,margin_sd,max(self.config.total_variance_floor,margin_sd),blended)


def create_holdout_manifest(path: Path, season: int, split: NFLResearchSplit, config: NFLV3Config, snapshot_hashes: dict[str,str]) -> dict[str,Any]:
    payload={"season":season,"holdout_start_week":split.holdout_start_week,"snapshot_hashes":dict(sorted(snapshot_hashes.items())),"model_version":V3_MODEL_VERSION,"config":asdict(config),"model_config_hash":config.configuration_hash,"created_at":datetime.now(timezone.utc).isoformat()}
    Path(path).write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n"); return payload


def verify_holdout_manifest(path: Path, config: NFLV3Config, snapshot_hashes: dict[str,str]) -> dict[str,Any]:
    manifest=json.loads(Path(path).read_text())
    if manifest.get("model_config_hash") != config.configuration_hash: raise ValueError("frozen V3 configuration differs from holdout manifest")
    if manifest.get("snapshot_hashes") != dict(sorted(snapshot_hashes.items())): raise ValueError("snapshot hashes differ from holdout manifest")
    return manifest
