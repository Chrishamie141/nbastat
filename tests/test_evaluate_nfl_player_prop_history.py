import json

from backtesting import evaluate_nfl_player_prop_history as history


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _week(snapshot_root, week=1):
    directory = snapshot_root / "nfl" / "2025" / f"week_{week:02d}"
    _write(directory / "games.json", [{"game_id": "g"}])
    _write(directory / "player_prop_odds.json", [{"game_id": "g"}])
    _write(directory / "player_prop_predictions.json", [
        {"season": 2025, "week": week, "readiness": "READY"},
        {"season": 2025, "week": week, "readiness": "NOT_READY_NO_PLAYER_DATA"},
    ])
    _write(directory / "player_stats.json", [{"game_id": "g", "player_id": "p"}])
    _write(directory / "player_identities.json", [{"game_id": "g", "canonical_player_id": "p"}])
    _write(directory / "manifest.json", {"schema_version": 1})
    return directory


def _evaluation(week=1):
    row = {"season": 2025, "week": week, "game_id": "g", "canonical_player_id": "p",
           "market": "receiving_yards", "line": 10.5, "side": "OVER", "bookmaker": "book",
           "model_probability": .6, "opposite_probability": .4, "push_probability": 0,
           "no_vig_market_probability": .55, "grade": "WIN", "profit": 1.0, "edge": .05,
           "american_odds": 100, "outcome": 11, "profit_units": 1.0}
    summary = {"accepted_quotes": 1, "quotes_with_predictions": 1, "gradeable_quotes": 1,
               "unique_opportunities": 1, "gradeable_unique_opportunities": 1,
               "excluded_unique_opportunities": 0, "outcome_aggregation": {"canonical_player_outcomes": 1}}
    return {"summary": summary, "quote_rows": [row], "opportunity_rows": [row],
            "exclusions": [], "calibration": {}, "edge_buckets": [], "breakdowns": {}}


def _audit(week=1):
    row = _evaluation(week)["opportunity_rows"][0]
    return {"summary": {"duplicate_prediction_keys": 0}, "rows": [row], "distributions": [],
            "validation_findings": [], "key_diagnostics": {"coherence": []}}


def test_season_artifacts_and_week_coverage_contract(tmp_path, monkeypatch):
    snapshots = tmp_path / "snapshots"
    _week(snapshots)
    monkeypatch.setattr(history, "evaluate", lambda *args, **kwargs: _evaluation())
    monkeypatch.setattr(history, "audit", lambda *args, **kwargs: _audit())
    monkeypatch.setattr(history, "write_audit_outputs", lambda *args, **kwargs: None)
    output = tmp_path / "output"

    report = history.run(season=2025, start_week=1, end_week=2, snapshot_root=snapshots,
                         output_dir=output, model_version="nfl_game_baseline_v3",
                         simulations=10, seed=1729, continue_on_error=True, validate=True)

    assert (output / "audit_validation_findings.json").exists()
    assert (output / "edge_bucket_metrics.json").exists()
    assert "audit_validation_findings.json" in report["audit_manifest.json"]["artifacts"]
    assert set(report["probability_direction_audit.json"]["evaluation_units"]) == {
        "all_side_specific_forecasts", "over_only", "under_only", "model_favored_side",
        "market_favored_side", "one_deterministic_forecast_per_base_opportunity"}
    assert report["probability_direction_audit.json"]["probability_lifecycle"]["passed"] is True
    assert set(report["market_metrics.json"][0]) >= {
        "model_brier", "market_brier", "model_minus_market_brier",
        "model_log_loss", "market_log_loss", "model_ece", "market_ece"}
    first, second = report["coverage_by_week.json"]
    assert set(first) >= {"games_present", "prediction_rows", "outcome_rows_present",
                          "gradeable_opportunities", "exclusions", "audit_findings", "run_action"}
    assert first["canonical_outcome_count"] == 1
    assert first["run_action"] == "RECOMPUTED"
    assert second["status"] == "MISSING_SNAPSHOTS"
    assert len(report["weekly_metrics.json"]) == 2


def test_resume_reports_reuse_and_invalidates_changed_inputs(tmp_path, monkeypatch):
    snapshots = tmp_path / "snapshots"
    directory = _week(snapshots)
    calls = {"evaluate": 0}

    def evaluate(*args, **kwargs):
        calls["evaluate"] += 1
        return _evaluation()

    monkeypatch.setattr(history, "evaluate", evaluate)
    monkeypatch.setattr(history, "audit", lambda *args, **kwargs: _audit())
    monkeypatch.setattr(history, "write_audit_outputs", lambda *args, **kwargs: None)
    output = tmp_path / "output"
    kwargs = dict(season=2025, start_week=1, end_week=1, snapshot_root=snapshots,
                  output_dir=output, model_version="nfl_game_baseline_v3",
                  simulations=10, seed=1729, continue_on_error=True, validate=True, resume=True)

    history.run(**kwargs)
    reused = history.run(**kwargs)
    assert calls["evaluate"] == 1
    assert reused["season_summary.json"]["reused_weeks"] == [1]
    assert reused["coverage_by_week.json"][0]["run_action"] == "REUSED"

    _write(directory / "manifest.json", {"schema_version": 2})
    invalidated = history.run(**kwargs)
    assert calls["evaluate"] == 2
    assert invalidated["season_summary.json"]["recomputed_weeks"] == [1]


def test_season_outputs_are_identical_for_shuffled_equivalent_inputs(tmp_path, monkeypatch):
    roots = [tmp_path / "snapshots_a", tmp_path / "snapshots_b"]
    for root in roots:
        _week(root)
    predictions = json.loads((roots[1] / "nfl/2025/week_01/player_prop_predictions.json").read_text(encoding="utf-8"))
    _write(roots[1] / "nfl/2025/week_01/player_prop_predictions.json", list(reversed(predictions)))
    monkeypatch.setattr(history, "evaluate", lambda *args, **kwargs: _evaluation())
    monkeypatch.setattr(history, "audit", lambda *args, **kwargs: _audit())
    monkeypatch.setattr(history, "write_audit_outputs", lambda *args, **kwargs: None)

    outputs = [tmp_path / "output_a", tmp_path / "output_b"]
    for snapshot_root, output_dir in zip(roots, outputs):
        history.run(season=2025, start_week=1, end_week=1, snapshot_root=snapshot_root,
                    output_dir=output_dir, model_version="nfl_game_baseline_v3",
                    simulations=10, seed=1729, continue_on_error=True, validate=True)

    names = json.loads((outputs[0] / "audit_manifest.json").read_text(encoding="utf-8"))["artifacts"]
    assert {name: (outputs[0] / name).read_bytes() for name in names} == {
        name: (outputs[1] / name).read_bytes() for name in names}


def test_fatal_week_is_artifact_first_and_does_not_block_later_week(tmp_path, monkeypatch):
    snapshots = tmp_path / "snapshots"
    _week(snapshots, 1)
    _week(snapshots, 2)
    evaluated = []

    def evaluate(_root, _season, start_week, *_args, **_kwargs):
        evaluated.append(start_week)
        return _evaluation(start_week)

    def audit(_root, _season, start_week, *_args, **_kwargs):
        report = _audit(start_week)
        if start_week == 1:
            report["validation_findings"] = [{"code": "SIDE_PROBABILITY_MISMATCH",
                                                "recoverable": False, "severity": "CRITICAL"}]
        return report

    monkeypatch.setattr(history, "evaluate", evaluate)
    monkeypatch.setattr(history, "audit", audit)
    monkeypatch.setattr(history, "write_audit_outputs", lambda *args, **kwargs: None)
    output = tmp_path / "output"
    report = history.run(season=2025, start_week=1, end_week=2, snapshot_root=snapshots,
                         output_dir=output, model_version="nfl_game_baseline_v3",
                         simulations=10, seed=1729, continue_on_error=True, validate=True)

    assert evaluated == [1, 2]
    assert [row["status"] for row in report["coverage_by_week.json"]] == ["INTEGRITY_FAILURE", "COMPLETE"]
    assert report["season_summary.json"]["fatal_integrity_weeks"] == [1]
    assert report["season_summary.json"]["final_exit_nonzero"] is True
    assert (output / "season_summary.json").exists()
    assert json.loads((output / "audit_validation_findings.json").read_text(encoding="utf-8"))[0]["week"] == 1
