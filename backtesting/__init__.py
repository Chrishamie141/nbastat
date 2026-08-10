"""Internal developer-only backtesting framework for SmartBetSports.

Keep the public convenience exports lazy.  Production imports the lightweight
NFL predictor from this package, but does not ship the complete replay
framework in its serverless bundle.
"""

from typing import Any

__all__ = ["BacktestConfig", "ReplayEngine"]


def __getattr__(name: str) -> Any:
    if name == "BacktestConfig":
        from .config import BacktestConfig

        return BacktestConfig
    if name == "ReplayEngine":
        from .replay_engine import ReplayEngine

        return ReplayEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
