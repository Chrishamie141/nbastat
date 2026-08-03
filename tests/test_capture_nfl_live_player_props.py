import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from nfl_providers import HttpJsonResponse
from backtesting import capture_nfl_live_player_props as live


GAME = {
    "game_id": "espn-401", "season": 2026, "week": 1, "league": "nfl",
    "home_team": "Buffalo Bills", "away_team": "Miami Dolphins",
    "kickoff_time": "2026-09-10T00:00:00Z", "status": "STATUS_SCHEDULED",
}
PLAYER = {
    "game_id": "espn-401", "player_id": "espn-athlete-1",
    "canonical_player_id": "espn-athlete-1", "player_name": "Test Runner",
    "normalized_player_name": "test runner", "team": "BUF", "source": "roster",
}
CAPTURE_TIME = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _week(tmp_path, *, identities=True):
    directory = tmp_path / "snapshots" / "nfl" / "2026" / "week_01"
    directory.mkdir(parents=True)
    (directory / "games.json").write_text(json.dumps([GAME]), encoding="utf-8")
    if identities:
        (directory / "player_identities.json").write_text(json.dumps([PLAYER]), encoding="utf-8")
    return directory


def test_plan_is_offline_and_zero_credit_outside_window(tmp_path, monkeypatch):
    _week(tmp_path, identities=False)
    monkeypatch.setattr(live, "_fetch_json_structured",
                        lambda *_: pytest.fail("network called"))
    report = live.plan_capture(snapshot_root=tmp_path / "snapshots", season=2026,
                               week=1, as_of="2026-08-02T00:00:00Z")
    assert report["status"] == "WAIT_OUTSIDE_CAPTURE_WINDOW"
    assert report["maximum_paid_credits"] == report["paid_credits_used"] == 0
    assert report["network_contacted"] is False


def test_identity_readiness_blocks_before_any_network_call(tmp_path, monkeypatch):
    _week(tmp_path, identities=False)
    monkeypatch.setattr(live, "_fetch_json_structured",
                        lambda *_: pytest.fail("network called"))
    report = live.capture(snapshot_root=tmp_path / "snapshots", cache_root=tmp_path / "cache",
                          season=2026, week=1, as_of="2026-09-09T00:00:00Z")
    assert report["status"] == "IDENTITIES_MISSING"
    assert report["action"] == "WAIT" and report["paid_requests"] == 0


def test_authorization_uses_three_credit_worst_case_per_ready_event(tmp_path):
    _week(tmp_path)
    plan = live.plan_capture(snapshot_root=tmp_path / "snapshots", season=2026,
                             week=1, as_of="2026-09-09T00:00:00Z")
    assert plan["games_ready"] == 1
    assert plan["maximum_credits_per_event"] == plan["maximum_paid_credits"] == 3
    with pytest.raises(live.LivePaidBudgetExceeded, match="allow-paid-fetch"):
        live.validate_authorization(plan, allow_paid_fetch=False, max_paid_credits=None)
    with pytest.raises(live.LivePaidBudgetExceeded, match="required"):
        live.validate_authorization(plan, allow_paid_fetch=True, max_paid_credits=None)
    with pytest.raises(live.LivePaidBudgetExceeded, match="exceeds"):
        live.validate_authorization(plan, allow_paid_fetch=True, max_paid_credits=2)
    live.validate_authorization(plan, allow_paid_fetch=True, max_paid_credits=3)


def test_plan_derives_identity_readiness_from_local_roster_evidence(tmp_path, monkeypatch):
    directory = _week(tmp_path, identities=False)
    roster = [{key: value for key, value in PLAYER.items()
               if key not in {"player_id", "canonical_player_id"}}]
    roster[0].update({"provider_player_id": "espn-athlete-1", "season": 2026, "week": 1,
                      "known_at": "2026-09-09T00:00:00Z",
                      "captured_at": "2026-09-09T00:00:00Z",
                      "data_as_of": "2026-09-09T00:00:00Z"})
    (directory / "roster_identities.json").write_text(json.dumps(roster), encoding="utf-8")
    monkeypatch.setattr(live, "_fetch_json_structured",
                        lambda *_: pytest.fail("network called"))
    plan = live.plan_capture(snapshot_root=tmp_path / "snapshots", season=2026,
                             week=1, as_of="2026-09-09T00:00:00Z")
    assert plan["status"] == "READY" and plan["identity_records"] == 1
    assert plan["identity_source"] == "derived_from_local_identity_evidence"
    assert not (directory / "player_identities.json").exists()
    report = live.materialize_identities(snapshot_root=tmp_path / "snapshots",
                                         season=2026, week=1)
    assert report["status"] == "IDENTITIES_MATERIALIZED"
    assert report["identity_records"] == 1
    assert len(report["artifact"]["sha256"]) == 64
    assert len(report["source_artifacts"]) == 1
    assert (directory / "player_identities.json").exists()


def test_live_capture_discovers_normalizes_pairs_caches_and_redacts(tmp_path, monkeypatch):
    directory = _week(tmp_path)
    secret = "live-secret-key-value"
    monkeypatch.setenv("THE_ODDS_API_KEY", secret)
    calls = []

    def fake_fetch(url):
        calls.append(url)
        parsed = urlparse(url)
        assert parse_qs(parsed.query)["apiKey"] == [secret]
        if parsed.path.endswith("/events"):
            return HttpJsonResponse([{
                "id": "odds-event-1", "sport_key": "americanfootball_nfl",
                "home_team": "Buffalo Bills", "away_team": "Miami Dolphins",
                "commence_time": "2026-09-10T00:00:00Z",
            }], 200, {"x-requests-last": "0", "x-requests-remaining": "99"})
        markets = []
        for market in live.LIVE_PLAYER_PROP_MARKETS:
            markets.append({"key": market, "last_update": "2026-09-09T00:00:00Z",
                            "outcomes": [
                                {"name": "Over", "description": "Test Runner", "point": 10.5, "price": -110},
                                {"name": "Under", "description": "Test Runner", "point": 10.5, "price": -110},
                            ]})
        return HttpJsonResponse({
            "id": "odds-event-1", "home_team": "Buffalo Bills",
            "away_team": "Miami Dolphins", "commence_time": "2026-09-10T00:00:00Z",
            "bookmakers": [{"key": "book", "markets": markets}],
        }, 200, {"x-requests-last": "3", "x-requests-remaining": "96"})

    monkeypatch.setattr(live, "_fetch_json_structured", fake_fetch)
    report = live.capture(snapshot_root=tmp_path / "snapshots", cache_root=tmp_path / "cache",
                          season=2026, week=1, as_of="2026-09-09T00:00:00Z",
                          allow_paid_fetch=True, max_paid_credits=3, _now=CAPTURE_TIME)
    assert report["status"] == "CAPTURED"
    assert report["paid_requests"] == 1 and report["paid_credits_used"] == 3
    assert report["rows_persisted"] == 6
    rows = json.loads((directory / "player_prop_odds.json").read_text(encoding="utf-8"))
    assert len(rows) == 6
    assert {row["selection"] for row in rows} == {"OVER", "UNDER"}
    assert {row["source"] for row in rows} == {"the-odds-api-live"}
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["datasets"]["player_prop_odds"]["source"] == "the-odds-api-live"
    assert secret not in json.dumps(report)
    assert all(secret.encode() not in path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())

    calls.clear()
    replay = live.capture(snapshot_root=tmp_path / "snapshots", cache_root=tmp_path / "cache",
                          season=2026, week=1, as_of="2026-09-09T00:00:00Z",
                          allow_paid_fetch=True, max_paid_credits=3, _now=CAPTURE_TIME)
    assert len(calls) == 1  # free discovery only; event odds came from raw cache
    assert replay["paid_requests"] == replay["paid_credits_used"] == 0


def test_capture_rejects_post_kickoff_without_network(tmp_path, monkeypatch):
    _week(tmp_path)
    monkeypatch.setattr(live, "_fetch_json_structured",
                        lambda *_: pytest.fail("network called"))
    report = live.capture(snapshot_root=tmp_path / "snapshots", cache_root=tmp_path / "cache",
                          season=2026, week=1, as_of="2026-09-10T00:00:01Z")
    assert report["games"][0]["status"] == "KICKED_OFF"
    assert report["paid_requests"] == 0


def test_empty_live_response_does_not_erase_existing_quotes(tmp_path, monkeypatch):
    directory = _week(tmp_path)
    existing = b'[{"preserved": true}]\n'
    (directory / "player_prop_odds.json").write_bytes(existing)
    monkeypatch.setenv("THE_ODDS_API_KEY", "secret")

    def fake_fetch(url):
        if urlparse(url).path.endswith("/events"):
            return HttpJsonResponse([{
                "id": "e1", "sport_key": "americanfootball_nfl",
                "home_team": "Buffalo Bills", "away_team": "Miami Dolphins",
                "commence_time": "2026-09-10T00:00:00Z",
            }], 200, {"x-requests-last": "0"})
        return HttpJsonResponse({"id": "e1", "bookmakers": []}, 200,
                                {"x-requests-last": "0"})

    monkeypatch.setattr(live, "_fetch_json_structured", fake_fetch)
    report = live.capture(snapshot_root=tmp_path / "snapshots", cache_root=tmp_path / "cache",
                          season=2026, week=1, as_of="2026-09-09T00:00:00Z",
                          allow_paid_fetch=True, max_paid_credits=3, _now=CAPTURE_TIME)
    assert report["status"] == "NO_COMPLETE_QUOTES"
    assert (directory / "player_prop_odds.json").read_bytes() == existing
    assert (directory / "live_player_prop_capture_audit.json").exists()


def test_stale_as_of_is_rejected_before_network(tmp_path, monkeypatch):
    _week(tmp_path)
    monkeypatch.setattr(live, "_fetch_json_structured",
                        lambda *_: pytest.fail("network called"))
    with pytest.raises(ValueError, match="within five minutes"):
        live.capture(snapshot_root=tmp_path / "snapshots", cache_root=tmp_path / "cache",
                     season=2026, week=1, as_of="2026-09-09T00:00:00Z",
                     allow_paid_fetch=True, max_paid_credits=3,
                     _now=datetime(2026, 9, 9, 0, 6, tzinfo=timezone.utc))
