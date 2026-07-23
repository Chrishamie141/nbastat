"""Command-line entrypoint for internal SmartBetSports backtests."""

from __future__ import annotations

import argparse

from .config import BacktestConfig
from .replay_engine import ReplayEngine


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for ``python -m backtesting.run_backtest``."""
    parser = argparse.ArgumentParser(description="Run an isolated internal prediction backtest.")
    parser.add_argument("--league", required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--start-week", type=int)
    parser.add_argument("--end-week", type=int)
    parser.add_argument("--markets", default="", help="Comma-separated markets to include.")
    parser.add_argument("--model-version", default="development")
    parser.add_argument("--export", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> None:
    """Run a configured chronological replay from the command line."""
    args = parse_args()
    markets = tuple(m for m in args.markets.split(",") if m)
    config = BacktestConfig(
        league=args.league,
        season=args.season,
        start_week=args.start_week,
        end_week=args.end_week,
        markets=markets,
        model_version=args.model_version,
        export=args.export,
    )
    ReplayEngine(config).run()


if __name__ == "__main__":
    main()
