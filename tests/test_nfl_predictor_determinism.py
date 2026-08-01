"""Process- and input-order regression coverage for NFL V2/V3 features."""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys

import pytest

from backtesting.nfl_game_predictor import NFLGameMarketPredictorV2
from backtesting.nfl_v3 import NFLGameMarketPredictorV3


def deterministic_case() -> tuple[dict, list[dict]]:
    target = {"game_id": "target", "season": 2025, "week": 5,
              "home_team": "BUF", "away_team": "MIA",
              "kickoff_time": "2025-10-01T20:00:00Z"}
    rows: list[dict] = []
    games = [
        (2024, 17, "z-prior", "2024-12-29T20:00:00Z", "BUF", "MIA", 21, 17),
        (2025, 1, "z-late-id", "2025-09-01T20:00:00Z", "BUF", "NYJ", 24, 20),
        (2025, 1, "a-early-id", "2025-09-01T20:00:00Z", "MIA", "DAL", 27, 23),
        (2025, 2, "m-two", "2025-09-08T20:00:00Z", "MIA", "BUF", 19, 16),
        (2025, 3, "b-three", "2025-09-15T20:00:00Z", "BUF", "DAL", 30, 14),
        (2025, 3, "c-three", "2025-09-15T20:00:00Z", "NYJ", "MIA", 10, 28),
    ]
    for season, week, game_id, stamp, home, away, home_score, away_score in games:
        common = {"game_id": game_id, "season": season, "week": week,
                  "completed_at": stamp, "data_as_of": stamp,
                  "record_role": "completed_game_history", "is_pregame": False}
        rows.extend((
            {**common, "team": home, "opponent": away, "home_away": "home",
             "points_for": home_score, "points_against": away_score},
            {**common, "team": away, "opponent": home, "home_away": "away",
             "points_for": away_score, "points_against": home_score},
        ))
    # Unknown chronology is deliberately present but remains ineligible.
    rows.append({"game_id": "missing-time", "season": 2025, "week": 4,
                 "team": "BUF", "opponent": "MIA", "home_away": "home",
                 "record_role": "completed_game_history", "is_pregame": False,
                 "points_for": 99, "points_against": 0})
    return target, rows


def serialized_projection(predictor: object, game: dict, rows: list[dict]) -> str:
    projection = predictor.project(game, rows)
    assert projection is not None
    return json.dumps(projection.output(home_team="BUF", away_team="MIA"),
                      sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("predictor_type", [NFLGameMarketPredictorV2, NFLGameMarketPredictorV3])
def test_projection_is_identical_for_shuffled_paired_history(predictor_type):
    game, rows = deterministic_case()
    expected = serialized_projection(predictor_type(), game, rows)
    for seed in range(30):
        shuffled = rows.copy()
        random.Random(seed).shuffle(shuffled)
        assert serialized_projection(predictor_type(), game, shuffled) == expected


PROCESS_SCRIPT = """
import json, os
from backtesting.nfl_game_predictor import NFLGameMarketPredictorV2
from backtesting.nfl_v3 import NFLGameMarketPredictorV3
case=json.loads(os.environ['NFL_DETERMINISM_CASE'])
result=[]
for predictor in (NFLGameMarketPredictorV2(), NFLGameMarketPredictorV3()):
    projection=predictor.project(case['game'], case['rows'])
    result.append(projection.output(home_team='BUF', away_team='MIA'))
print(json.dumps(result, sort_keys=True, separators=(',', ':')))
"""


def process_projection(game: dict, rows: list[dict], hash_seed: str) -> str:
    environment = os.environ.copy()
    environment["PYTHONHASHSEED"] = hash_seed
    environment["NFL_DETERMINISM_CASE"] = json.dumps({"game": game, "rows": rows})
    return subprocess.check_output(
        [sys.executable, "-c", PROCESS_SCRIPT], env=environment, text=True
    ).strip()


def test_projection_is_identical_across_hash_seeds_and_repeated_processes():
    game, rows = deterministic_case()
    outputs = [process_projection(game, rows, seed) for seed in ("0", "1", "2", "42", "random")]
    # Twenty additional fresh interpreters guard against process-local drift.
    outputs.extend(process_projection(game, rows, str(index)) for index in range(20))
    assert len(set(outputs)) == 1

