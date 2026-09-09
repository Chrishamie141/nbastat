from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path

import pytest

from backend.app.services import social_marketing as social
from backend.app.services import social_operations_service as owner
from backend.app.services.social import analytics, content, events, opportunities, scheduler, scoring, settings
from backend.app.services.social.media import prompts
from backend.app.services.social.media.assets import LocalMediaStorage
from backend.app.services.social.media.graphic_renderer import render_graphic
from backend.app.services.social.media.service import generate_media
from backend.app.services.social.publisher import OfficialX


@pytest.fixture
def engine(tmp_path, monkeypatch):
    at = datetime(2032, 9, 9, 17, tzinfo=timezone.utc)
    monkeypatch.setenv("SOCIAL_DATABASE_URL", "sqlite:///" + (tmp_path / "social-v2.db").as_posix())
    monkeypatch.setenv("SOCIAL_SOURCE_SIGNING_KEY", "social-engine-test-signing-key-123456789")
    monkeypatch.setenv("SOCIAL_CAMPAIGN_START", at.date().isoformat())
    monkeypatch.setenv("SOCIAL_AUTOMATION_ENABLED", "true")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("SOCIAL_DRY_RUN", "true")
    monkeypatch.setenv("SOCIAL_AUTO_PUBLISH", "false")
    monkeypatch.setenv("SOCIAL_AI_IMAGES_ENABLED", "false")
    monkeypatch.setenv("SOCIAL_DETERMINISTIC_GRAPHICS_ENABLED", "true")
    monkeypatch.setenv("SOCIAL_MEDIA_LOCAL_DIR", str(tmp_path / "media"))
    monkeypatch.delenv("VERCEL", raising=False)
    social.initialize()
    return at, tmp_path


def source(at, *, result=None, market=True):
    game = {
        "game_id": "game-1", "away_team": "Buffalo Bills", "home_team": "Pittsburgh Steelers",
        "kickoff_time": (at + timedelta(hours=2)).isoformat(),
        "prediction": {"winner": "Buffalo Bills", "probability": .674, "generated_at": (at - timedelta(hours=1)).isoformat(),
                       "model_version": "verified-v1", "model_hash": "model-hash", "prediction_hash": "prediction-hash"},
    }
    if market:
        game["market"] = {"snapshot_id": "market-1", "probability": .541, "edge": .133,
                          "movement": .04, "underdog": "Buffalo Bills", "bookmaker": "approved-book"}
    if result:
        game["kickoff_time"] = (at - timedelta(hours=4)).isoformat()
        game["prediction"]["generated_at"] = (at - timedelta(hours=6)).isoformat()
        game["final"] = {"away_score": 24, "home_score": 17, "retrieved_at": at.isoformat()}
        game["grade"] = {"result": result, "graded_at": at.isoformat(), "qualified_wager": False}
    return {"verified_at": at.isoformat(), "preseason": {"record": {"WIN": 11, "LOSS": 4, "PUSH": 1}, "predictions": 16},
            "regular": {"week": 1, "predictions": 1, "scheduled": 1, "graded": 1 if result else 0,
                        "winner_record": {"WIN": int(result == "WIN"), "LOSS": int(result == "LOSS"), "PUSH": int(result == "PUSH")}},
            "coverage_games": int(market), "operations": {"games": [game]},
            "claims_policy": "predictions_are_not_wagers;small_sample_not_future_performance"}


def store_source(value):
    social.store_source(value)
    return social.sha(value)


def test_schema_migrates_legacy_day_key_to_multi_post(engine):
    at, _ = engine; source_id = store_source(source(at))
    values = lambda number: (f"post-{number}", "campaign", "GAME_PREVIEW|0", f"caption {number}", source_id,
                             at.isoformat(), at.isoformat(), None, None, "DRAFT", None,
                             social.sha(f"caption {number}"), "signature", at.date().isoformat())
    with social.connection() as connection:
        connection.execute("INSERT INTO social_posts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values(1))
        connection.execute("INSERT INTO social_posts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values(2))
        assert connection.execute("SELECT COUNT(*) FROM social_posts WHERE day_key=?", (at.date().isoformat(),)).fetchone()[0] == 2
        assert connection.execute("SELECT MAX(version) FROM social_schema_migrations").fetchone()[0] == 3


def test_postgres_initialization_locks_before_schema_ddl(monkeypatch):
    statements = []

    class FakeConnection:
        postgres = True

        def execute(self, sql, values=()):
            statements.append((sql, values))

    @contextmanager
    def fake_connection():
        yield FakeConnection()

    monkeypatch.setattr(social, "connection", fake_connection)
    monkeypatch.setattr(social, "initialize_schema", lambda connection: connection.execute("SCHEMA MIGRATION"))

    social.initialize()

    assert statements[0] == (
        "SELECT pg_advisory_xact_lock(?)",
        (social.SOCIAL_SCHEMA_LOCK_ID,),
    )
    assert statements[-1][0] == "SCHEMA MIGRATION"


def test_scoring_is_explainable_and_deterministic(engine):
    at, _ = engine
    event = events.discover("source", source(at), lambda: at)[2]
    result = scoring.score(event, settings.environment_defaults(), clock=lambda: at)
    assert result.score >= result.threshold and result.decision.value in {"QUEUE", "POST_NOW"}
    assert any("confidence" in reason.lower() for reason in result.reasons)


def test_discovery_is_idempotent_and_stores_skip_reasons(engine, monkeypatch):
    at, _ = engine; value = source(at); source_id = store_source(value)
    monkeypatch.setenv("SOCIAL_OPPORTUNITY_SCORE_THRESHOLD", "100")
    with social.connection() as connection:
        config = settings.load_settings(connection)
        opportunities.discover(connection, source_id, value, config, social.sha, lambda: at)
        opportunities.discover(connection, source_id, value, config, social.sha, lambda: at)
        count = connection.execute("SELECT COUNT(*) FROM social_opportunities").fetchone()[0]
        skipped = connection.execute("SELECT COUNT(*) FROM social_opportunities WHERE status='SKIPPED' AND skip_reason IS NOT NULL").fetchone()[0]
    assert count > 0 and skipped > 0


@pytest.mark.parametrize("result,event_type", [("WIN", "PREDICTION_WIN"), ("LOSS", "PREDICTION_LOSS"), ("PUSH", "PREDICTION_PUSH")])
def test_receipts_require_frozen_prediction_and_grade(engine, result, event_type):
    at, _ = engine
    supported = events.discover("source", source(at, result=result), lambda: at)
    assert event_type in {item["event_type"] for item in supported}
    missing = source(at, result=result); missing["operations"]["games"][0]["prediction"] = None
    assert event_type not in {item["event_type"] for item in events.discover("source", missing, lambda: at)}


def test_no_market_comparison_without_verified_market(engine):
    at, _ = engine
    discovered = events.discover("source", source(at, market=False), lambda: at)
    assert "MODEL_MARKET_DISAGREEMENT" not in {item["event_type"] for item in discovered}
    with pytest.raises(ValueError, match="market"):
        content.deterministic_caption({"post_type": "MODEL_VS_MARKET", "away_team": "A", "home_team": "B",
                                       "winner": "A", "model_probability": .6})


def test_ai_writer_failure_uses_grounded_deterministic_fallback(engine, monkeypatch):
    monkeypatch.setenv("SOCIAL_AI_COPY_ENABLED", "true")
    context = {"post_type": "AI_PICK", "away_team": "Bills", "home_team": "Steelers",
               "winner": "Bills", "model_probability": .674, "source_entities": ["Bills", "Steelers"]}
    class Broken:
        def __call__(self, _context):
            raise TimeoutError("provider unavailable")
    caption, provider = content.compose(context, lambda: Broken())
    assert provider == "deterministic_fallback" and "67.4%" in caption
    with pytest.raises(ValueError, match="unsupported number"):
        content.validate_caption("Bills model read: 99.9%", context)


def test_image_prompt_forbids_generated_authoritative_text(engine):
    prompt = prompts.creative_prompt({"away_team": "Bills", "home_team": "Steelers"}, "AI_PICK")
    assert "Do not render any words" in prompt and "deterministic data overlay" in prompt


def test_deterministic_overlay_and_media_metadata(engine):
    pytest.importorskip("PIL")
    at, tmp_path = engine; context = {"post_type": "AI_PICK", "away_team": "Buffalo Bills",
        "home_team": "Pittsburgh Steelers", "winner": "Buffalo Bills", "model_probability": .674,
        "market_probability": .541, "edge": .133}
    payload = render_graphic(context, "AI_PICK")
    from PIL import Image
    assert Image.open(BytesIO(payload)).size == (1600, 900)
    opportunity = {"opportunity_id": "opp-media", "content_type": "AI_PICK", "game_id": "game-1", "source_hash": "source-hash"}
    with social.connection() as connection:
        config = settings.load_settings(connection); config["ai_images_enabled"] = False
        asset = generate_media(connection, opportunity, {"post_id": None}, context, config, social.sha,
                               storage=LocalMediaStorage(tmp_path / "media"), clock=lambda: at)
        row = connection.execute("SELECT * FROM social_media_assets WHERE media_asset_id=?", (asset["media_asset_id"],)).fetchone()
    assert row["status"] == "READY" and row["source_hash"] == "source-hash" and Path(row["storage_url"]).exists()


def test_image_and_video_feature_flags_and_caps(engine, monkeypatch):
    config = settings.environment_defaults()
    assert config["external_auto_replies_enabled"] is False and config["video_enabled"] is False
    monkeypatch.setenv("SOCIAL_MAX_IMAGES_PER_DAY", "0")
    at, _ = engine
    with social.connection() as connection:
        with pytest.raises(RuntimeError, match="DAILY_CAP"):
            generate_media(connection, {"opportunity_id": "o", "content_type": "AI_PICK", "game_id": "g", "source_hash": "h"},
                           {"post_id": None}, {"winner": "A", "model_probability": .6}, settings.environment_defaults(), social.sha,
                           storage=LocalMediaStorage(), clock=lambda: at)


def test_queue_materialization_pause_and_caption_grounding(engine):
    at, _ = engine; value = source(at); source_id = store_source(value)
    with social.connection() as connection:
        config = settings.load_settings(connection)
        opportunities.discover(connection, source_id, value, config, social.sha, lambda: at)
        row = connection.execute("SELECT * FROM social_opportunities WHERE content_type='AI_PICK'").fetchone()
        post = scheduler.materialize(connection, row["opportunity_id"], sha=social.sha, sign=social.sign, clock=lambda: at)
        assert post["status"] == "READY"
        with pytest.raises(ValueError, match="unsupported number"):
            scheduler.edit_caption(connection, post["post_id"], "Bills at 98.2%", social.sha, social.sign)
        settings.update_settings(connection, {"paused": True}, "owner")
        assert scheduler.due(connection, settings.load_settings(connection), lambda: at) == []


def test_queue_dry_run_builds_deterministic_media_without_external_writes(engine):
    pytest.importorskip("PIL")
    at, _ = engine
    store_source(source(at))
    social.discover_opportunities(lambda: at)
    result = social.process_queue(
        clock=lambda: at + timedelta(minutes=16),
        limit=1,
        client_factory=lambda: pytest.fail("dry run created an X client"),
    )
    assert result["processed"] == 1 and result["items"][0]["result"]["status"] == "DRY_RUN"
    with social.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM social_media_assets WHERE status='READY'").fetchone()[0] == 1


def test_daily_worker_never_falls_back_to_generic_content(engine, monkeypatch):
    monkeypatch.setattr(social, "discover_opportunities", lambda clock: {"discovered": 0})
    monkeypatch.setattr(social, "process_queue", lambda **kwargs: {"processed": 0, "items": []})
    monkeypatch.setattr(social, "generate", lambda *args, **kwargs: pytest.fail("generic post generated"))
    result = social.daily(lambda: engine[0])
    assert result["post"] is None and result["result"]["status"] == "NO_DUE_OPPORTUNITY"


def test_ambiguous_x_delivery_is_never_retried(engine, monkeypatch):
    at, _ = engine; store_source(source(at)); post = social.generate(lambda: at)
    monkeypatch.setenv("SOCIAL_DRY_RUN", "false"); monkeypatch.setenv("DRY_RUN", "false"); monkeypatch.setenv("SOCIAL_AUTO_PUBLISH", "true")
    class Client:
        def verify_company(self): pass
        def post(self, _text): raise TimeoutError("ambiguous")
    assert social.publish_one(post["post_id"], lambda: at, Client)["status"] == "UNKNOWN"
    assert social.publish_one(post["post_id"], lambda: at, lambda: pytest.fail("blind retry"))["status"] == "UNKNOWN"


def test_safe_pre_delivery_failures_retry_at_most_three_times(engine, monkeypatch):
    at, _ = engine; store_source(source(at)); post = social.generate(lambda: at)
    monkeypatch.setenv("SOCIAL_DRY_RUN", "false"); monkeypatch.setenv("DRY_RUN", "false"); monkeypatch.setenv("SOCIAL_AUTO_PUBLISH", "true")
    attempts = []

    class Client:
        def verify_company(self):
            attempts.append("verify")
            raise ConnectionError("pre-delivery authentication check failed")

    for expected in (1, 2, 3):
        assert social.publish_one(post["post_id"], lambda: at, Client)["status"] == "FAILED"
        with social.connection() as connection:
            claim = connection.execute(
                "SELECT attempt_count,last_error_code FROM social_delivery_claims WHERE post_id=?",
                (post["post_id"],),
            ).fetchone()
        assert claim["attempt_count"] == expected
        assert claim["last_error_code"] == "ACCOUNT_OR_AUTH_VERIFICATION_FAILED"

    assert social.publish_one(post["post_id"], lambda: at, lambda: pytest.fail("retry cap bypassed"))["status"] == "FAILED"
    assert len(attempts) == 3


def test_x_media_upload_and_company_verification_use_official_endpoints(engine, monkeypatch):
    monkeypatch.setenv("X_EXPECTED_USER_ID", "company")
    calls = []
    class Response:
        def __init__(self, status, body): self.status_code = status; self._body = body
        def json(self): return self._body
    class Session:
        def get(self, url, **kwargs): calls.append(("GET", url, kwargs)); return Response(200, {"data": {"id": "company"}})
        def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            return Response(201, {"data": {"id": "12345"}}) if url.endswith("/2/media/upload") else Response(200, {})
    client = OfficialX(Session()); client.verify_company(); assert client.upload_image(b"png", alt_text="Accessible graphic") == "12345"
    assert calls[0][1].endswith("/2/users/me")
    upload = next(call for call in calls if call[1].endswith("/2/media/upload"))
    metadata = next(call for call in calls if call[1].endswith("/2/media/metadata"))
    assert upload[2]["data"]["media_category"] == "tweet_image"
    assert metadata[2]["json"] == {"id": "12345", "metadata": {"alt_text": {"text": "Accessible graphic"}}}
    assert all("/1.1/" not in call[1] and "upload.twitter.com" not in call[1] for call in calls)


def test_x_video_uses_current_v2_chunked_endpoints(engine, monkeypatch):
    calls = []
    class Response:
        def __init__(self, status, body): self.status_code = status; self._body = body
        def json(self): return self._body
    class Session:
        def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            if url.endswith("/initialize"):
                return Response(200, {"data": {"id": "67890"}})
            if url.endswith("/finalize"):
                return Response(200, {"data": {"id": "67890", "processing_info": {"state": "succeeded"}}})
            return Response(200, {"data": {}})
    assert OfficialX(Session()).upload_video(b"video") == "67890"
    urls = [url for _, url, _ in calls]
    assert urls == [
        "https://api.x.com/2/media/upload/initialize",
        "https://api.x.com/2/media/upload/67890/append",
        "https://api.x.com/2/media/upload/67890/finalize",
    ]
    assert calls[0][2]["json"]["media_category"] == "tweet_video"


def test_metrics_are_null_when_x_plan_does_not_expose_them(engine):
    at, _ = engine; source_id = store_source(source(at))
    with social.connection() as connection:
        connection.execute("INSERT INTO social_posts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("published", "c", "GAME_PREVIEW|0", "caption", source_id, at.isoformat(), at.isoformat(), at.isoformat(), "123", "PUBLISHED", None, social.sha("caption"), "s", at.date().isoformat()))
        analytics.store_snapshot(connection, "published", "123", {"likes": 2, "replies": 1, "reposts": 1}, social.sha, lambda: at)
        report = analytics.report(connection)
    assert report["totals"]["impressions"] is None and report["byMediaType"][0]["engagementRate"] is None


def test_owner_social_routes_are_internal_and_pause_is_persisted(engine):
    from backend.app.main import app
    from backend.app.services.entitlement_service import require_internal_access
    routes = [route for route in app.routes if getattr(route, "path", "").startswith("/api/internal/operations/social")]
    assert routes and all(any(dependency.call is require_internal_access for dependency in route.dependant.dependencies) for route in routes)
    result = owner.pause(True, "owner@example.test")
    assert result["paused"] is True and owner.summary()["engineState"] == "PAUSED"
