import json

from backtesting.historical_provider import HistoricalSnapshotProvider


def _row(game_id, team, stamp, *, season=2025, week=1, opponent="OPP"):
    row = {
        "game_id": game_id, "team": team, "opponent": opponent,
        "season": season, "week": week, "record_role": "completed_game_history",
        "is_pregame": False, "points_for": 24, "points_against": 17,
    }
    if stamp is not None:
        row["completed_at"] = row["data_as_of"] = stamp
    return row


def _write(root, rows, *, season=2025, week=1):
    directory = root / "nfl" / str(season) / f"week_{week:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "team_stats.json").write_text(json.dumps(rows))


def test_canonical_team_history_uses_game_chronology_and_keeps_pairs_adjacent(tmp_path):
    rows = [
        _row("a-late", "MIA", "2025-09-14T20:00:00Z", week=2, opponent="BUF"),
        _row("z-early", "BUF", "2025-09-07T20:00:00Z", opponent="NYJ"),
        _row("a-late", "BUF", "2025-09-14T20:00:00Z", week=2, opponent="MIA"),
        _row("z-early", "NYJ", "2025-09-07T20:00:00Z", opponent="BUF"),
    ]
    _write(tmp_path, rows)

    result = HistoricalSnapshotProvider(tmp_path).canonical_team_history("nfl", "2025")

    assert [row["game_id"] for row in result] == ["z-early", "z-early", "a-late", "a-late"]
    assert [row["team"] for row in result] == ["BUF", "NYJ", "BUF", "MIA"]


def test_canonical_team_history_is_shuffle_and_duplicate_independent(tmp_path):
    rows = [
        _row("z", "BUF", "2025-09-01T00:00:00Z"),
        _row("a", "MIA", "2025-09-02T00:00:00Z"),
    ]
    first_root, second_root = tmp_path / "first", tmp_path / "second"
    _write(first_root, rows + [dict(rows[0])])
    _write(second_root, list(reversed(rows)) + [dict(rows[0])])

    first = HistoricalSnapshotProvider(first_root).canonical_team_history("nfl", "2025")
    second = HistoricalSnapshotProvider(second_root).canonical_team_history("nfl", "2025")

    assert first == second
    assert len(first) == 2


def test_prior_seasons_and_missing_timestamps_have_deterministic_fallback(tmp_path):
    _write(tmp_path, [_row("prior", "KC", None, season=2024, week=18)], season=2024, week=18)
    _write(tmp_path, [
        _row("b-missing", "BUF", None, week=2),
        _row("a-missing", "MIA", None, week=2),
        _row("dated", "NYJ", "2025-09-01T00:00:00Z", week=1),
    ], week=2)

    result = HistoricalSnapshotProvider(tmp_path).canonical_team_history("nfl", "2025")

    assert [row["game_id"] for row in result] == ["prior", "dated", "a-missing", "b-missing"]


def test_canonical_team_history_cache_is_defensively_copied(tmp_path):
    _write(tmp_path, [_row("game", "BUF", "2025-09-01T00:00:00Z")])
    provider = HistoricalSnapshotProvider(tmp_path)
    first = provider.canonical_team_history("nfl", "2025")
    first[0]["team"] = "MUTATED"
    first.append({"game_id": "injected"})

    assert provider.canonical_team_history("nfl", "2025") == [
        _row("game", "BUF", "2025-09-01T00:00:00Z")
    ]
