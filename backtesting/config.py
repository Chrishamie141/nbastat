"""Configuration objects for internal prediction backtests."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_ROOT / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
RESULTS_DIR = PACKAGE_ROOT / "results"
LOGS_DIR = PACKAGE_ROOT / "logs"
DEFAULT_DB_PATH = DATA_DIR / "backtests.db"


class PredictionMode(str, Enum):
    """Replay prediction modes."""

    BETTING = "BETTING"
    STATISTICAL = "STATISTICAL"


@dataclass(frozen=True)
class BacktestConfig:
    """Immutable configuration for one chronological backtest run."""

    league: str
    season: str
    start_week: int | None = None
    end_week: int | None = None
    markets: tuple[str, ...] = field(default_factory=tuple)
    model_version: str = "development"
    export: bool = True
    db_path: Path = DEFAULT_DB_PATH
    data_dir: Path = SNAPSHOTS_DIR
    results_dir: Path = RESULTS_DIR
    prediction_mode: PredictionMode | str = PredictionMode.BETTING

    def mode(self) -> PredictionMode:
        """Return the configured replay mode as an enum."""
        if isinstance(self.prediction_mode, PredictionMode):
            return self.prediction_mode
        return PredictionMode(str(self.prediction_mode).upper())

    def normalized_markets(self) -> tuple[str, ...]:
        """Return selected markets with whitespace removed and lowercase applied."""
        return tuple(m.strip().lower() for m in self.markets if m.strip())
