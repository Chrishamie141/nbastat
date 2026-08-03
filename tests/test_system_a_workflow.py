from __future__ import annotations

import json
import gzip

from backtesting.system_a.workflow import build_workflow, verify_directories
from backtesting.system_a.inventory import scan_snapshots
from pathlib import Path


def test_empty_frozen_source_blocks_historical_m1_without_fabrication(tmp_path):
    snapshots = tmp_path / "snapshots"
    first = tmp_path / "first"; second = tmp_path / "second"
    missing_players = tmp_path / "missing-players.csv"
    build_workflow(snapshot_root=snapshots, output_dir=first, seasons=(2025,), players_path=missing_players)
    build_workflow(snapshot_root=snapshots, output_dir=second, seasons=(2025,), players_path=missing_players)
    summary = json.loads((first / "reconciliation_summary.json").read_text())
    assert summary["network_contacted"] is False
    assert summary["milestone_1_acceptance"] is False
    assert summary["milestone_1_blocker"] == "MISSING_PLAY_BY_PLAY"
    with gzip.open(first / "canonical_play_events.json.gz", "rt", encoding="utf-8") as handle:
        assert json.load(handle) == []
    assert verify_directories(first, second)["deterministic"] is True


def test_repository_historical_inventory_reconciles_identity_and_reports_pbp_gap():
    root = Path(__file__).parents[1] / "backtesting" / "data" / "snapshots"
    scan = scan_snapshots(root, (2023, 2024, 2025))
    summary = scan["coverage_summary"]
    assert summary["total_games"] > 0
    assert summary["identity_resolved_rows"] == summary["raw_completed_stat_rows"]
    assert summary["play_by_play_status"] == "MISSING"
    assert all(row["games_parsed"] == row["games_present"] for row in summary["seasons"])
