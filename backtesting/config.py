"""Configuration objects for internal prediction backtests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_ROOT / "data"
RESULTS_DIR = PACKAGE_ROOT / "results"
LOGS_DIR = PACKAGE_ROOT / "logs"
DEFAULT_DB_PATH = DATA_DIR / "backtests.db"


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
    data_dir: Path = DATA_DIR
    results_dir: Path = RESULTS_DIR

    def normalized_markets(self) -> tuple[str, ...]:
        """Return selected markets with whitespace removed and lowercase applied."""
        return tuple(m.strip().lower() for m in self.markets if m.strip())
