"""Model and run version metadata for repeatable backtests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

from .config import BacktestConfig
from .utils import git_commit_hash, stable_hash, utc_now_iso

PREDICTION_ENGINE_VERSION = "production-adapter-v1"


@dataclass(frozen=True)
class RunMetadata:
    """Version information captured once for every immutable backtest run."""

    run_id: str
    model_version: str
    league: str
    season: str
    git_commit_hash: str
    prediction_engine_version: str
    configuration_hash: str
    date: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize metadata for database inserts and JSON reports."""
        return asdict(self)


def create_run_metadata(config: BacktestConfig) -> RunMetadata:
    """Create unique version metadata without requiring Git metadata."""
    payload = {
        "league": config.league,
        "season": config.season,
        "start_week": config.start_week,
        "end_week": config.end_week,
        "markets": config.normalized_markets(),
        "model_version": config.model_version,
    }
    return RunMetadata(
        run_id=str(uuid4()),
        model_version=config.model_version,
        league=config.league,
        season=config.season,
        git_commit_hash=git_commit_hash(),
        prediction_engine_version=PREDICTION_ENGINE_VERSION,
        configuration_hash=stable_hash(payload),
        date=utc_now_iso(),
    )
