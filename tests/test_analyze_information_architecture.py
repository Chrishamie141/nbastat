from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_analyze_only_offers_new_analysis_choices():
    source = (ROOT / "frontend/app/analyze/page.jsx").read_text(encoding="utf-8")

    for label in ("Game Winners", "Same Game Parlay", "Multi-Game Parlay", "Fantasy Football"):
        assert label in source
    for stale_label in ("View Parlay History", "Grade NFL Parlays", "View NFL Performance Report", "Choose an action"):
        assert stale_label not in source


def test_game_winners_has_non_parlay_view_choices():
    source = (ROOT / "frontend/app/analyze/page.jsx").read_text(encoding="utf-8")

    assert "All Weekly Picks" in source
    assert "Sunday Picks" in source
    assert "Select Individual Game" in source
    assert "Winner selections are optional" in source


def test_history_and_performance_remain_separate_routes():
    history = (ROOT / "frontend/app/history/page.jsx").read_text(encoding="utf-8")
    performance = (ROOT / "frontend/app/performance/page.jsx").read_text(encoding="utf-8")

    assert "Past Games" in history
    assert "Grade saved predictions" in performance
    assert "Performance & grading" in performance
