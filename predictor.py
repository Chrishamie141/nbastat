"""Backward-compatible import for the dedicated NBA model.

New code should import :class:`NBAPlayerStatModel` from
``backend.app.services.nba.model``.  Keeping this alias avoids breaking the
existing CLI while eliminating the ambiguous shared-algorithm ownership.
"""

from backend.app.services.nba.model import NBAPlayerStatModel

PlayerStatPredictor = NBAPlayerStatModel

__all__ = ["PlayerStatPredictor"]
