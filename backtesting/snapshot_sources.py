"""Historical snapshot collection sources and raw-response caching for ESPN, The Odds API, optional NFL data, weather, and local JSON."""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError

from .config import DATA_DIR

DATASET_METHODS = {
    "games": "fetch_games",
    "odds": "fetch_odds",
    "weather": "fetch_weather",
    "injuries": "fetch_injuries",
    "player_stats": "fetch_player_stats",
    "team_stats": "fetch_team_stats",
    "outcomes": "fetch_outcomes",
}


class ProviderUnavailable(RuntimeError):
    """Raised when a provider cannot supply a requested historical dataset."""


class HistoricalSnapshotSource(Protocol):
    """Provider contract for building normalized historical snapshots."""

    name: str
    supported_datasets: set[str]

    def fetch_games(self, league: str, season: str, week: int, week_range: tuple[str, str]) -> list[dict[str, Any]]: ...
    def fetch_odds(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
    def fetch_weather(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
    def fetch_injuries(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
    def fetch_player_stats(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
    def fetch_team_stats(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
    def fetch_outcomes(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


@dataclass
class RawCache:
    """Small JSON cache to avoid repeated paid historical provider requests."""

    root: Path = DATA_DIR / "raw_cache"
    overwrite: bool = False

    def path(self, provider: str, league: str, season: str, week: int, dataset: str) -> Path:
        return self.root / provider / league.lower() / str(season) / f"week_{int(week):02d}" / f"{dataset}.json"

    def get_or_fetch(self, provider: str, league: str, season: str, week: int, dataset: str, fetcher: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
        path = self.path(provider, league, season, week, dataset)
        if path.exists() and not self.overwrite:
            return json.loads(path.read_text())
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                rows = fetcher()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
                return rows
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                last_exc = exc
                time.sleep(0.1 * (2 ** attempt))
        raise ProviderUnavailable(f"{provider} {dataset} request failed after retries: {last_exc}")


def _redact(text: str) -> str:
    for key in ("THE_ODDS_API_KEY", "ODDS_API_KEY", "OPENWEATHER_API_KEY", "OPEN_WEATHER_API_KEY"):
        value = os.getenv(key)
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


class ExistingNflProviderSource:
    """Adapter around existing live NFL helper functions.

    The existing helpers are current-data endpoints. They are intentionally not
    used as historical substitutes, except where an explicit external/local
    export has been configured elsewhere.
    """

    name = "existing-nfl"
    supported_datasets: set[str] = set()

    def _unavailable(self, dataset: str) -> list[dict[str, Any]]:
        raise ProviderUnavailable(
            f"existing NFL clients do not expose true historical {dataset}; use local-json/local-csv export or a paid historical provider plan"
        )

    def fetch_games(self, league: str, season: str, week: int, week_range: tuple[str, str]) -> list[dict[str, Any]]:
        return self._unavailable("games")

    def fetch_odds(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._unavailable("odds")

    def fetch_weather(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._unavailable("weather")

    def fetch_injuries(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._unavailable("injuries")

    def fetch_player_stats(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._unavailable("player_stats")

    def fetch_team_stats(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._unavailable("team_stats")

    def fetch_outcomes(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._unavailable("outcomes")


class EspnSnapshotSource:
    name = "espn"
    supported_datasets = {"games", "player_stats", "team_stats", "outcomes", "injuries"}

    def __init__(self):
        from nfl_providers import EspnNflProvider, JsonRawCache
        self.provider = EspnNflProvider(cache=JsonRawCache(DATA_DIR / "raw_cache"))

    def fetch_games(self, league: str, season: str, week: int, week_range: tuple[str, str]) -> list[dict[str, Any]]:
        if league.lower() != "nfl":
            raise ProviderUnavailable("ESPN snapshot adapter currently supports NFL only")
        return self.provider.fetch_games(season, week)

    def fetch_player_stats(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.provider.fetch_player_stats(season, week, games)

    def fetch_team_stats(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.provider.fetch_team_stats(season, week, games)

    def fetch_outcomes(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.provider.fetch_outcomes(season, week, games)

    def fetch_injuries(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = self.provider.fetch_injuries(season, week, games)
        if not rows:
            print("WARNING: no verified NFL injury provider returned data; writing optional empty injuries dataset")
        return rows


class TheOddsApiSnapshotSource:
    name = "odds-api"
    supported_datasets = {"odds"}

    def __init__(self, hours_before_kickoff: int = 24):
        from nfl_providers import TheOddsApiNflProvider, JsonRawCache
        self.provider = TheOddsApiNflProvider(cache=JsonRawCache(DATA_DIR / "raw_cache"))
        self.hours_before_kickoff = int(hours_before_kickoff)

    def fetch_odds(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for game in games:
            kickoff_raw = game.get("kickoff_time")
            if not kickoff_raw:
                continue
            kickoff = datetime.fromisoformat(str(kickoff_raw).replace("Z", "+00:00"))
            snapshot_dt = kickoff - timedelta(hours=self.hours_before_kickoff)
            if snapshot_dt >= kickoff:
                raise ProviderUnavailable("historical odds snapshot timestamp must be before kickoff")
            snapshot_time = snapshot_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            
            try:
                game_rows = self.provider.fetch_odds(season, week, [game], snapshot_time=snapshot_time)
            except Exception as exc:
                raise ProviderUnavailable(str(exc)) from exc
            for row in game_rows:
                captured = row.get("captured_at") or row.get("snapshot_timestamp") or snapshot_time
                if datetime.fromisoformat(str(captured).replace("Z", "+00:00")) >= kickoff:
                    raise ProviderUnavailable(f"odds_after_kickoff: provider returned odds captured after kickoff for {game.get('game_id')}")
                row.setdefault("snapshot_timestamp", snapshot_time)
                row.setdefault("data_as_of", captured)
                row.setdefault("is_pregame", True)
                row.setdefault("source", "the-odds-api-historical")
                rows.append(row)
        return rows


class NflOfficialSnapshotSource:
    name = "nfl-official"
    supported_datasets: set[str] = set()
    def __init__(self):
        from nfl_providers import NflOfficialProvider
        self.provider = NflOfficialProvider()


class HistoricalWeatherSnapshotSource:
    name = "historical-weather"
    supported_datasets = {"weather"}
    def fetch_weather(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise ProviderUnavailable("no genuine historical weather/archive provider is configured; current OpenWeather data was not substituted")


class OpenWeatherSnapshotSource(HistoricalWeatherSnapshotSource):
    name = "openweather"
    def fetch_weather(self, league: str, season: str, week: int, week_range: tuple[str, str], games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise ProviderUnavailable("OpenWeather live adapter is disabled for historical snapshots; use historical-weather or local-json export")


@dataclass
class LocalJsonSnapshotSource:
    """Load historical provider exports from JSON files under a directory."""

    source_dir: Path
    name: str = "local-json"
    supported_datasets: set[str] = field(default_factory=lambda: set(DATASET_METHODS))

    def _load(self, league: str, season: str, week: int, dataset: str) -> list[dict[str, Any]]:
        base = self.source_dir / league.lower() / str(season) / f"week_{int(week):02d}"
        combined = base / "snapshot.json"
        path = base / f"{dataset}.json"
        if path.exists():
            data = json.loads(path.read_text())
            return data if isinstance(data, list) else list(data.get(dataset, []))
        if combined.exists():
            data = json.loads(combined.read_text())
            return list(data.get(dataset, []))
        raise ProviderUnavailable(f"local JSON export missing {dataset}: {path}")

    def fetch_games(self, league: str, season: str, week: int, week_range: tuple[str, str]) -> list[dict[str, Any]]:
        return self._load(league, season, week, "games")

    def __getattr__(self, name: str):
        if name.startswith("fetch_"):
            dataset = name.removeprefix("fetch_")
            return lambda league, season, week, week_range, games=None: self._load(league, season, week, dataset)
        raise AttributeError(name)


def create_sources(spec: str | None, odds_hours_before_kickoff: int = 24) -> list[HistoricalSnapshotSource]:
    sources: list[HistoricalSnapshotSource] = []
    names = [p.strip() for p in (spec or "odds-api,espn,nfl-official,local-json").split(",") if p.strip()]
    for name in names:
        if name == "existing-nfl":
            sources.append(ExistingNflProviderSource())
        elif name == "espn":
            sources.append(EspnSnapshotSource())
        elif name == "odds-api":
            sources.append(TheOddsApiSnapshotSource(odds_hours_before_kickoff))
        elif name == "nfl-official":
            sources.append(NflOfficialSnapshotSource())
        elif name == "openweather":
            sources.append(OpenWeatherSnapshotSource())
        elif name == "historical-weather":
            sources.append(HistoricalWeatherSnapshotSource())
        elif name == "local-json":
            sources.append(LocalJsonSnapshotSource(Path(os.getenv("BACKTESTING_LOCAL_EXPORT_DIR", DATA_DIR / "provider_exports"))))
        else:
            raise ProviderUnavailable(f"Unknown snapshot provider '{_redact(name)}'")
    return sources
