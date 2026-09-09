"""Non-destructive social ledger migration and repository helpers."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any


LEGACY_POST_COLUMNS = (
    "post_id", "campaign", "category", "content", "source_id", "generated_at",
    "scheduled_at", "published_at", "x_post_id", "status", "failure_reason",
    "content_hash", "signature", "day_key",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sqlite_has_unique_day_key(connection) -> bool:
    for index in connection.execute("PRAGMA index_list(social_posts)").fetchall():
        if not index["unique"]:
            continue
        columns = [row["name"] for row in connection.execute(f"PRAGMA index_info('{index['name']}')").fetchall()]
        if columns == ["day_key"]:
            return True
    return False


def _remove_day_key_uniqueness(connection) -> None:
    """Allow distinct posts per day without deleting any legacy history."""
    if connection.postgres:
        connection.execute("ALTER TABLE social_posts DROP CONSTRAINT IF EXISTS social_posts_day_key_key")
        return
    if not _sqlite_has_unique_day_key(connection):
        return
    columns = ",".join(LEGACY_POST_COLUMNS)
    connection.execute("""CREATE TABLE social_posts_multi_day (
        post_id TEXT PRIMARY KEY,campaign TEXT NOT NULL,category TEXT NOT NULL,
        content TEXT NOT NULL,source_id TEXT NOT NULL REFERENCES social_sources(source_id),
        generated_at TEXT NOT NULL,scheduled_at TEXT NOT NULL,published_at TEXT,x_post_id TEXT,
        status TEXT NOT NULL,failure_reason TEXT,content_hash TEXT NOT NULL UNIQUE,
        signature TEXT NOT NULL,day_key TEXT NOT NULL)""")
    connection.execute(f"INSERT INTO social_posts_multi_day({columns}) SELECT {columns} FROM social_posts")
    connection.execute("DROP TABLE social_posts")
    connection.execute("ALTER TABLE social_posts_multi_day RENAME TO social_posts")


def _ensure_delivery_attempt_count(connection) -> None:
    if connection.postgres:
        connection.execute("ALTER TABLE social_delivery_claims ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 1")
        return
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(social_delivery_claims)").fetchall()}
    if "attempt_count" not in columns:
        connection.execute("ALTER TABLE social_delivery_claims ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 1")


def initialize_schema(connection) -> None:
    """Upgrade the dedicated social database in place.

    This follows the repository's existing idempotent-initializer convention and
    records an explicit schema version.  It never touches predictions.db.
    """
    connection.execute("""CREATE TABLE IF NOT EXISTS social_schema_migrations(
        version INTEGER PRIMARY KEY,description TEXT NOT NULL,applied_at TEXT NOT NULL)""")
    applied = connection.execute(
        "SELECT MAX(version) AS version FROM social_schema_migrations"
    ).fetchone()
    if applied and int(applied["version"] or 0) >= 3:
        return
    _remove_day_key_uniqueness(connection)
    connection.execute("CREATE INDEX IF NOT EXISTS social_posts_status_schedule ON social_posts(status,scheduled_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS social_posts_day_status ON social_posts(day_key,status,published_at)")
    connection.execute("""CREATE TABLE IF NOT EXISTS social_settings(
        setting_key TEXT PRIMARY KEY,value_json TEXT NOT NULL,updated_at TEXT NOT NULL,updated_by TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS social_events(
        event_id TEXT PRIMARY KEY,event_type TEXT NOT NULL,game_id TEXT,source_id TEXT NOT NULL
        REFERENCES social_sources(source_id),prediction_id TEXT,model_version TEXT,model_hash TEXT,
        market_snapshot_id TEXT,grade_evidence_id TEXT,event_at TEXT NOT NULL,evidence_json TEXT NOT NULL,
        evidence_hash TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(event_type,game_id,evidence_hash))""")
    connection.execute("CREATE INDEX IF NOT EXISTS social_events_source_type ON social_events(source_id,event_type,event_at)")
    connection.execute("""CREATE TABLE IF NOT EXISTS social_opportunities(
        opportunity_id TEXT PRIMARY KEY,event_id TEXT NOT NULL REFERENCES social_events(event_id),
        game_id TEXT,content_type TEXT NOT NULL,score REAL NOT NULL,threshold REAL NOT NULL,
        decision TEXT NOT NULL,score_reasons_json TEXT NOT NULL,idempotency_key TEXT NOT NULL UNIQUE,
        source_hash TEXT NOT NULL,status TEXT NOT NULL,scheduled_at TEXT,created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,skip_reason TEXT)""")
    connection.execute("CREATE INDEX IF NOT EXISTS social_opportunities_queue ON social_opportunities(status,scheduled_at,score)")
    connection.execute("CREATE INDEX IF NOT EXISTS social_opportunities_event ON social_opportunities(event_id)")
    connection.execute("""CREATE TABLE IF NOT EXISTS social_post_details(
        post_id TEXT PRIMARY KEY REFERENCES social_posts(post_id),event_id TEXT REFERENCES social_events(event_id),
        opportunity_id TEXT REFERENCES social_opportunities(opportunity_id),game_id TEXT,post_type TEXT NOT NULL,
        media_asset_ids_json TEXT NOT NULL DEFAULT '[]',media_type TEXT NOT NULL DEFAULT 'text',
        reply_to_x_post_id TEXT,score REAL,score_reasons_json TEXT NOT NULL DEFAULT '[]',
        source_context_json TEXT NOT NULL,source_hash TEXT NOT NULL,integrity_hash TEXT NOT NULL,
        alt_text TEXT,owner_edited INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
    connection.execute("CREATE INDEX IF NOT EXISTS social_post_details_opportunity ON social_post_details(opportunity_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS social_post_details_event ON social_post_details(event_id)")
    connection.execute("""CREATE TABLE IF NOT EXISTS social_media_assets(
        media_asset_id TEXT PRIMARY KEY,opportunity_id TEXT REFERENCES social_opportunities(opportunity_id),
        post_id TEXT REFERENCES social_posts(post_id),game_id TEXT,media_type TEXT NOT NULL,template TEXT NOT NULL,
        provider TEXT NOT NULL,provider_model TEXT,generation_quality TEXT,prompt_hash TEXT NOT NULL,
        source_hash TEXT NOT NULL,storage_key TEXT,storage_url TEXT,status TEXT NOT NULL,
        width INTEGER,height INTEGER,duration_seconds REAL,alt_text TEXT,remote_media_id TEXT,
        version INTEGER NOT NULL DEFAULT 1,cost_estimate REAL,error_code TEXT,retry_count INTEGER NOT NULL DEFAULT 0,
        generated_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
    connection.execute("CREATE INDEX IF NOT EXISTS social_media_assets_post ON social_media_assets(post_id,generated_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS social_media_assets_opportunity ON social_media_assets(opportunity_id,media_type,version)")
    connection.execute("""CREATE TABLE IF NOT EXISTS social_metrics(
        metric_id TEXT PRIMARY KEY,post_id TEXT NOT NULL REFERENCES social_posts(post_id),x_post_id TEXT,
        collected_at TEXT NOT NULL,impressions REAL,likes REAL,replies REAL,reposts REAL,quotes REAL,
        bookmarks REAL,url_clicks REAL,profile_clicks REAL,followers REAL,availability_json TEXT NOT NULL,
        raw_hash TEXT NOT NULL,UNIQUE(post_id,collected_at))""")
    connection.execute("CREATE INDEX IF NOT EXISTS social_metrics_post_time ON social_metrics(post_id,collected_at)")
    connection.execute("""CREATE TABLE IF NOT EXISTS social_engagement_queue(
        engagement_id TEXT PRIMARY KEY,kind TEXT NOT NULL,x_post_id TEXT,conversation_id TEXT,
        game_id TEXT,source_id TEXT NOT NULL REFERENCES social_sources(source_id),candidate_text TEXT NOT NULL,
        content_hash TEXT NOT NULL UNIQUE,status TEXT NOT NULL,reason TEXT,scheduled_at TEXT,
        reply_x_post_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
    connection.execute("CREATE INDEX IF NOT EXISTS social_engagement_review ON social_engagement_queue(status,created_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS social_engagement_source ON social_engagement_queue(source_id)")
    connection.execute("""CREATE TABLE IF NOT EXISTS social_delivery_claims(
        idempotency_key TEXT PRIMARY KEY,post_id TEXT NOT NULL REFERENCES social_posts(post_id),
        claimed_at TEXT NOT NULL,state TEXT NOT NULL,remote_media_ids_json TEXT NOT NULL DEFAULT '[]',
        remote_post_id TEXT,last_error_code TEXT,attempt_count INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL)""")
    _ensure_delivery_attempt_count(connection)
    connection.execute("CREATE INDEX IF NOT EXISTS social_delivery_claims_post ON social_delivery_claims(post_id)")
    connection.execute(
        "INSERT INTO social_schema_migrations(version,description,applied_at) VALUES(2,?,?) ON CONFLICT(version) DO NOTHING",
        ("multi-post event engine and durable media ledger", utc_now()),
    )
    connection.execute(
        "INSERT INTO social_schema_migrations(version,description,applied_at) VALUES(3,?,?) ON CONFLICT(version) DO NOTHING",
        ("bounded retry accounting for safe pre-delivery failures", utc_now()),
    )
    if connection.postgres:
        tables = (
            "social_sources", "social_posts", "social_publish_days", "social_schema_migrations",
            "social_settings", "social_events", "social_opportunities", "social_post_details",
            "social_media_assets", "social_metrics", "social_engagement_queue", "social_delivery_claims",
        )
        for table in tables:
            connection.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            connection.execute(f"REVOKE ALL ON TABLE {table} FROM anon, authenticated")


def rows(connection, query: str, values: tuple = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query, values).fetchall()]


def one(connection, query: str, values: tuple = ()) -> dict[str, Any] | None:
    row = connection.execute(query, values).fetchone()
    return dict(row) if row else None


def json_value(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def decoded(value: str | None, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback
