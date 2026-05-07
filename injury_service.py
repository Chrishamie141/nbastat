from __future__ import annotations

import unicodedata
from pathlib import Path

INJURY_FILE = "injured_players.txt"


def _normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().split())


class InjuryService:
    def __init__(self, file_path: str | Path = INJURY_FILE):
        self.file_path = Path(file_path)
        self._injured_map = self._load_injured_players()

    def _load_injured_players(self) -> dict[str, str]:
        injured: dict[str, str] = {}

        if not self.file_path.exists():
            return injured

        for raw_line in self.file_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            injured[_normalize_name(line)] = line

        return injured

    def is_injured(self, player_name: str) -> bool:
        return _normalize_name(player_name) in self._injured_map

    def canonical_name(self, player_name: str) -> str:
        key = _normalize_name(player_name)
        return self._injured_map.get(key, player_name)
