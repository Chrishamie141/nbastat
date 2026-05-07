from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Iterable


DEFAULT_INJURED_PLAYERS_FILE = Path(__file__).resolve().parent / "injured_players.txt"


def _normalize_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return without_accents.casefold().strip()


class InjuryService:
    def __init__(self, injured_players_file: Path | str | None = None):
        self.injured_players_file = Path(injured_players_file) if injured_players_file else DEFAULT_INJURED_PLAYERS_FILE
        self._injured_players = self._load_injured_players()

    def _load_injured_players(self) -> set[str]:
        if not self.injured_players_file.exists():
            return set()

        injured_players: set[str] = set()
        for raw_line in self.injured_players_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            injured_players.add(_normalize_name(line))
        return injured_players

    def is_injured(self, player_name: str) -> bool:
        return _normalize_name(player_name) in self._injured_players

    def exclude_injured(self, players: Iterable[str], team_mode: bool = False) -> list[str]:
        all_players = list(players)
        filtered = [player for player in all_players if not self.is_injured(player)]
        if not team_mode and len(filtered) != len(all_players):
            print("Warning: selected player appears in injured_players.txt")
        return filtered


__all__ = ["InjuryService", "DEFAULT_INJURED_PLAYERS_FILE"]
