from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.nfl_automation_cron import router
from backend.app.services import nfl_server_automation as automation
from backend.app.services.nfl_product_service import _season_type
from nfl_providers import HttpJsonResponse


NOW = datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc)


def _context(_season):
    return {"season": 2026, "seasonType": "regular", "week": 1,
            "displayWeek": 1, "providerWeek": 1, "hasUpcoming": True}


def _game(kickoff):
    return {"game_id": "espn-1", "home_team": "PHI", "away_team": "DAL",
            "kickoff_time": automation.iso(kickoff), "status": "scheduled",
            "season": 2026, "season_type": "regular", "week": 1,
            "display_week": 1, "provider_week": 1, "provider": "espn"}


def _event(kickoff, source):
    markets = []
    for key, outcomes in (
        ("h2h", [{"name": "PHI", "price": -120}, {"name": "DAL", "price": 110}]),
        ("spreads", [{"name": "PHI", "price": -110, "point": -2.5},
                     {"name": "DAL", "price": -110, "point": 2.5}]),
        ("totals", [{"name": "Over", "price": -110, "point": 47.5},
                    {"name": "Under", "price": -110, "point": 47.5}]),
    ):
        markets.append({"key": key, "last_update": automation.iso(source), "outcomes": outcomes})
    return {"id": "odds-1", "home_team": "Philadelphia Eagles", "away_team": "Dallas Cowboys",
            "commence_time": automation.iso(kickoff),
            "bookmakers": [{"key": "testbook", "title": "Test Book", "markets": markets}]}


def test_phase_normalization_supports_entire_nfl_season():
    assert _season_type("pre") == "preseason"
    assert _season_type("reg") == "regular"
    assert _season_type("playoffs") == "postseason"


def test_calendar_guided_context_checks_only_nearby_regular_weeks():
    calls = []

    def schedule(_season, week, phase):
        calls.append((phase, week))
        kickoff = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)
        return [_game(kickoff)] if (phase, week) == ("regular", 11) else []

    context = automation.resolve_active_context(
        2026, at=datetime(2026, 11, 20, tzinfo=timezone.utc), schedule_loader=schedule)
    assert context["seasonType"] == "regular"
    assert context["week"] == 11
    assert len(calls) <= 3


def test_tick_is_bounded_idempotent_and_coverage_first(monkeypatch):
    kickoff = NOW + timedelta(hours=24)
    monkeypatch.setenv("NFL_AUTOMATION_ENABLED", "true")
    monkeypatch.setattr(automation, "_schedule", lambda *_args: [_game(kickoff)])
    calls = []

    def fetcher(url, timeout):
        calls.append((url, timeout))
        return HttpJsonResponse([_event(kickoff, NOW - timedelta(minutes=1))], 200,
                                {"x-requests-remaining": "97", "x-requests-used": "3",
                                 "x-requests-last": "3"})

    first = automation.tick(clock=lambda: NOW, fetcher=fetcher,
                            context_resolver=_context, api_key="test-key")
    second = automation.tick(clock=lambda: NOW, fetcher=fetcher,
                             context_resolver=_context, api_key="test-key")
    assert first["state"] == "HEALTHY"
    assert first["network_contacted"] is True
    assert first["games"] == {"espn-1": "COMPLETE"}
    assert second["state"] == "IDLE"
    assert second["network_contacted"] is False
    assert len(calls) == 1
    report = automation.status()
    assert report["game_coverage"] == {"total": 1, "covered": 1}
    assert report["checkpoint_counts"]["COMPLETE"] == 1
    assert report["checkpoint_counts"]["PENDING"] == 2
    assert report["publishing_enabled"] is False
    assert report["model_mutation_enabled"] is False


def test_opening_capture_runs_early_in_week_then_remains_idempotent(monkeypatch):
    kickoff = NOW + timedelta(days=2)
    monkeypatch.setenv("NFL_AUTOMATION_ENABLED", "true")
    monkeypatch.setattr(automation, "_schedule", lambda *_args: [_game(kickoff)])
    calls = []

    def fetcher(url, timeout):
        calls.append((url, timeout))
        return HttpJsonResponse([_event(kickoff, NOW - timedelta(minutes=1))], 200,
                                {"x-requests-remaining": "97", "x-requests-used": "3",
                                 "x-requests-last": "3"})

    first = automation.tick(clock=lambda: NOW, fetcher=fetcher,
                            context_resolver=_context, api_key="test-key")
    second = automation.tick(clock=lambda: NOW, fetcher=fetcher,
                             context_resolver=_context, api_key="test-key")
    assert first["state"] == "HEALTHY"
    assert first["network_contacted"] is True
    assert second["state"] == "IDLE"
    assert second["network_contacted"] is False
    assert len(calls) == 1


def test_tick_stays_idle_before_the_weekly_opening_window(monkeypatch):
    kickoff = NOW + timedelta(days=8)
    monkeypatch.setenv("NFL_AUTOMATION_ENABLED", "true")
    monkeypatch.setattr(automation, "_schedule", lambda *_args: [_game(kickoff)])
    result = automation.tick(clock=lambda: NOW,
        fetcher=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("provider called before opening window")),
        context_resolver=_context, api_key="test-key")
    assert result["state"] == "IDLE"
    assert result["network_contacted"] is False


def test_opening_capture_retries_when_sportsbook_market_is_not_posted(monkeypatch):
    kickoff = NOW + timedelta(days=2)
    monkeypatch.setenv("NFL_AUTOMATION_ENABLED", "true")
    monkeypatch.setattr(automation, "_schedule", lambda *_args: [_game(kickoff)])
    calls = []

    def fetcher(url, timeout):
        calls.append((url, timeout))
        payload = [] if len(calls) == 1 else [_event(kickoff, NOW + timedelta(hours=6, minutes=-1))]
        return HttpJsonResponse(payload, 200,
                                {"x-requests-remaining": str(100 - len(calls) * 3),
                                 "x-requests-used": str(len(calls) * 3),
                                 "x-requests-last": "3"})

    first = automation.tick(clock=lambda: NOW, fetcher=fetcher,
                            context_resolver=_context, api_key="test-key")
    early = automation.tick(clock=lambda: NOW + timedelta(hours=5), fetcher=fetcher,
                            context_resolver=_context, api_key="test-key")
    retry = automation.tick(clock=lambda: NOW + timedelta(hours=6), fetcher=fetcher,
                            context_resolver=_context, api_key="test-key")

    assert first["games"] == {"espn-1": "NO_MARKET"}
    assert early["state"] == "IDLE"
    assert early["network_contacted"] is False
    assert retry["games"] == {"espn-1": "COMPLETE"}
    assert len(calls) == 2
    report = automation.status()
    assert report["game_coverage"] == {"total": 1, "covered": 1}
    assert report["checkpoint_counts"]["COMPLETE"] == 1


def test_missing_key_fails_closed_without_network(monkeypatch):
    kickoff = NOW + timedelta(hours=24)
    monkeypatch.setenv("NFL_AUTOMATION_ENABLED", "true")
    monkeypatch.setattr(automation, "_schedule", lambda *_args: [_game(kickoff)])
    result = automation.tick(clock=lambda: NOW,
        fetcher=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("provider called")),
        context_resolver=_context, api_key="")
    assert result["state"] == "AUTH_ERROR"
    assert result["network_contacted"] is False


def test_kickoff_boundary_blocks_market_storage(monkeypatch):
    kickoff = NOW
    monkeypatch.setenv("NFL_AUTOMATION_ENABLED", "true")
    automation.initialize()
    from backend.app.database import get_db_connection
    with get_db_connection() as connection:
        checkpoint = {"experiment_key": "test", "game_id": "espn-1", "home_team": "PHI",
                      "away_team": "DAL", "kickoff": automation.iso(kickoff),
                      "kickoff_epoch": kickoff.timestamp(), "offset_minutes": 60,
                      "deadline_epoch": (kickoff + timedelta(minutes=5)).timestamp()}
        result = automation._store_game(connection, checkpoint,
            [_event(kickoff, NOW - timedelta(minutes=1))], 1, NOW - timedelta(seconds=1), lambda: NOW)
    assert result == "KICKOFF_BLOCKED"


def test_cron_routes_require_dedicated_secret(monkeypatch):
    monkeypatch.setenv("NFL_AUTOMATION_SECRET", "private-nfl-cron-secret-value-1234")
    monkeypatch.setattr(automation, "tick", lambda: {"state": "IDLE"})
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.post("/api/cron/nfl-active-season").status_code == 401
    response = client.post("/api/cron/nfl-active-season",
        headers={"authorization": "Bearer private-nfl-cron-secret-value-1234"})
    assert response.status_code == 200
    assert response.json() == {"state": "IDLE"}


def test_cron_install_rejects_non_https_and_short_secrets():
    import pytest
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        automation.install_supabase_cron(base_url="http://localhost", secret="short")
