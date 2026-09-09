# SmartBetSports Social Operations

## Architecture

The existing `backend/app/services/social_marketing.py` module remains the
compatibility facade and retains signed source verification, freshness checks,
copy integrity, company-account verification, durable pre-write claims,
`UNKNOWN` delivery reconciliation, preview blocking and dry-run controls.

The event engine lives under `backend/app/services/social/`:

- `events.py` creates immutable, evidence-linked sports events. A final result
  can create a result receipt but can never be converted into a pregame event.
- `opportunities.py` and `scoring.py` make explainable deterministic posting
  decisions and persist skipped reasons.
- `content.py` builds grounded copy. Optional OpenAI wording receives only a
  whitelisted context and always falls back to a deterministic template.
- `scheduler.py` materializes a resumable multi-post queue.
- `media/` separates creative artwork from the deterministic overlay containing
  every team name, probability, market value, edge, record and score.
- `publisher.py` extends the verified OAuth 1.0a company client with current X
  API v2 image upload, v2 alt-text metadata, chunked v2 video upload, replies and
  metrics reads. It does not call the retired v1.1 media host.
- `analytics.py` stores real metric snapshots and preserves `null` for metrics
  unavailable on the configured X plan.
- `engagement.py` creates approval-first reply candidates. Bulk replies and
  automated follow/unfollow behavior do not exist.

Social data consumes frozen prediction evidence. It never writes to prediction,
grading, experiment, market or game-result tables.

## Schema migration

`social_marketing.initialize()` applies schema versions 2-3 idempotently to the
separate `SOCIAL_DATABASE_URL`. It preserves `social_sources`, `social_posts`,
`social_publish_days` and every legacy post. The old `day_key UNIQUE` constraint
is removed while `content_hash` and new event/opportunity idempotency keys retain
duplicate protection. New normalized tables are:

- `social_schema_migrations`
- `social_settings`
- `social_events`
- `social_opportunities`
- `social_post_details`
- `social_media_assets`
- `social_metrics`
- `social_engagement_queue`
- `social_delivery_claims`

On PostgreSQL, RLS is enabled and `anon`/`authenticated` grants are revoked for
all social tables. These are server-only ledgers. Do not expose the service role
or `SOCIAL_DATABASE_URL` to frontend code.

## Media storage and rendering

Local development uses `.runtime/social/media`, which is ignored by Git. Vercel
rejects local media storage because its filesystem is ephemeral. Production must
set `SOCIAL_MEDIA_STORAGE_PROVIDER=supabase` and configure a private bucket via
`SUPABASE_URL`, server-only `SUPABASE_SERVICE_ROLE_KEY`, and
`SOCIAL_MEDIA_STORAGE_BUCKET`. The owner API proxies authenticated previews; the
bucket does not need to be public.

AI image generation is off until `SOCIAL_AI_IMAGES_ENABLED=true` and
`OPENAI_API_KEY` are configured. `SOCIAL_OPENAI_IMAGE_MODEL` defaults to the
current premium `gpt-image-2.5-sunburst` Image API model and remains explicit so
a deployment can select another supported OpenAI image model. The AI prompt forbids text,
numbers, marks and logos. Pillow adds the verified overlay at 1600x900 and checks
format, dimensions and required values before storage.

Videos are deterministic 10-20 second H.264 MP4s rendered with FFmpeg from the
validated graphic. Set `SOCIAL_VIDEO_ENABLED=true` only after FFmpeg is present
at `SOCIAL_FFMPEG_PATH`. A missing renderer holds the item safely; it never
publishes malformed media. Optional narration is deliberately not enabled.

Generated assets are immutable versions. Regeneration creates a new asset row
and storage object. Estimated cost appears only when the operator supplies a
reviewed `SOCIAL_IMAGE_COST_ESTIMATE` or `SOCIAL_VIDEO_COST_ESTIMATE`.

The media integration follows X's current `/2/media/upload`,
`/2/media/upload/initialize`, `/{id}/append`, `/{id}/finalize`, and
`/2/media/metadata` interfaces. X plan restrictions remain authoritative; an
unavailable endpoint is reported as a provider failure rather than simulated.

## Safe rollout

1. Deploy the schema/code with `SOCIAL_AUTOMATION_ENABLED=true`,
   `SOCIAL_DRY_RUN=true`, `SOCIAL_AUTO_PUBLISH=false`, videos off, metrics off and
   external replies off.
2. Open `/internal/operations/social` as the internal owner.
3. Run **Discover**, inspect queued and skipped reasoning, then **Process Queue**.
   Dry-run processing makes zero public X writes.
4. Configure durable production storage and enable AI images only after a test
   asset renders and previews correctly.
5. Verify the X company user ID and existing OAuth read/write permissions.
6. Turn off dry run and enable auto publish only after explicit review. Preview
   and development deployments remain prohibited from publishing.

For unattended production discovery, enable
`SOCIAL_SERVER_SOURCE_REFRESH_ENABLED=true`. Each worker run then verifies the
frozen Week 3 hash and reads current canonical NFL evidence before it scores any
opportunity; a mismatch or incomplete evidence blocks the run.

**PAUSE ALL SOCIAL** is available at the top of Social Operations. It writes a
persisted kill switch and audit entry. The environment-level emergency controls
remain `SOCIAL_AUTO_PUBLISH=false`, `SOCIAL_SCHEDULER_ENABLED=false`, or
`SOCIAL_DRY_RUN=true`; revoking the X access token is the final external stop.

An `UNKNOWN` X delivery must be reconciled against the company account and exact
caption. Never clear the claim or retry it blindly. Media upload failures that
occur before tweet creation are marked failed/reviewable without pretending a
public post exists. Explicit owner retries are capped at three delivery attempts
and are allowed only for failures that occurred before a tweet-creation request;
ambiguous public writes remain permanently reconciliation-only.

## Metrics and replies

X metric availability depends on the account and API access tier. The UI displays
"Not available from current X API access" rather than manufacturing zeroes.
Analytics influence social opportunity scoring only after adequate sample sizes;
they never change the sports prediction model.

Direct-mention candidates and relevant external-conversation candidates can be
stored for owner approval. `SOCIAL_AUTO_REPLIES_ENABLED` defaults false. There is
no bulk reply mode, promotional reply loop, or follow/unfollow automation.
