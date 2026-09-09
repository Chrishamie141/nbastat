# SmartBets company X marketing

## Safety contract

This subsystem uses only the official X API. There is no browser automation, scraping,
mass reply, follow, like, unsolicited mention, or engagement-spam feature. No real X post
was sent during development. `DRY_RUN` defaults to true; the scheduler itself defaults
to disabled. Neither generating a draft nor synchronizing evidence authorizes posting.

Templates are editorial code, not an LLM completion. All numeric claims are interpolated
from signed, programmatically verified SmartBets evidence. Preseason predictions and
qualified wagers are named separately. No subscriber counts or invented ROI are used.
The preseason record is a small historical sample, never a promised future win rate.

## Architecture / durable storage

- `backend/app/services/social_marketing.py`: verified-source synchronization, rotating
  templates, signing/validation, duplicate protection and official X API client.
- `backtesting/social_marketing.py`: local/operator commands.
- `backend/app/api/social_cron.py`: authenticated Vercel scheduler endpoint.
- `vercel.json`: daily production cron at **13:00 UTC** (9am New York during daylight
  saving time, 8am during standard time). Timing is not a real-time guarantee.

The compatibility ledger began with three dedicated tables:

| Table | Purpose |
| --- | --- |
| `social_sources` | Signed, hash-addressed verified statistical snapshots and verification time |
| `social_posts` | Post/campaign/category, content, source reference, generation/scheduling/publication times, X ID, status, failure reason, content hash/signature |
| `social_publish_days` | One durable global publication claim per UTC day, including uncertain deliveries |

Local development uses
a separate SQLite file; hosted Vercel requires a dedicated durable PostgreSQL database via
`SOCIAL_DATABASE_URL`. It never falls back to `DATABASE_URL` or `predictions.db`. The
application is deliberately blocked on Vercel if configured with SQLite. Initialize tables
explicitly with the operator command; the public API does not run migrations.

Use a dedicated PostgreSQL server role and schema/database. Initialization enables RLS;
no anonymous/browser policies are installed. Grant the runtime server role only SELECT,
INSERT and UPDATE on these tables and appropriate schema access. For a role distinct
from the table owner, install reviewed server-role RLS policies. Do not expose the social
database connection to browser clients or grant a browser role access to signing secrets.
The current environment does not have a configured hosted social database; real PostgreSQL
integration/deployment verification remains an operator prerequisite.

The production-grade event engine, multi-post queue, media ledger, analytics,
settings and engagement approval schema are documented in
[`docs/social-operations.md`](social-operations.md). Legacy records remain visible
after the version-2 in-place migration; the obsolete one-post-per-day constraint
is removed without weakening per-event, per-content and per-delivery idempotency.

Uniqueness constraints and PostgreSQL advisory locks / SQLite immediate transactions
serialize daily generation and publication. A durable `PUBLISHING` claim commits before
any X post request. A crash cannot blindly replay a post. Exact content hashes plus
normalized similarity comparison block identical/nearly identical drafts, including
versions that change only numbers or URLs. Campaigns end after the approved fourteen-day
window; there is no endless recycled-content loop.

## Verified source flow

`sync-source --week-db ...` reads the separate regular-season experiment and verifies
each prediction's content hash. It opens Week 3 **read-only**, verifies all sixteen frozen
JSONs against the known baseline hash, verifies pre-kickoff generation times, and
recomputes the prediction record against persisted finals/grades. It exports only an
allowlist of aggregate fields; no user information, subscriber counts or credentials.

The source is HMAC-signed using `SOCIAL_SOURCE_SIGNING_KEY`. Sources older than 36 hours
or with invalid signatures/hashes are rejected. Publishing re-renders the exact template
and validates the stored post signature/content against its source. Editing a draft or
CTA after generation invalidates it rather than silently publishing changed claims.

Vercel cannot read the laptop's SQLite files. Run the Week 1 worker with `--sync-social`
to refresh the hosted verified-source snapshot hourly while the laptop is running, or
schedule the `sync-source` command at least daily on the authoritative host. Use the same
social database and signing key on the host and Vercel. An unavailable/stale feed blocks
posting; it does not manufacture statistics. The source must continue refreshing during
the campaign even after all weekly games are final. The worker must remain awake/online,
or be moved to an explicitly approved always-on environment with the authoritative files.

## X developer setup and credentials

Official references: [Create Posts integration](https://docs.x.com/x-api/posts/manage-tweets/integrate),
[OAuth 1.0a](https://docs.x.com/fundamentals/authentication/oauth-1-0a/overview),
[X automation rules](https://help.x.com/en/rules-and-policies/x-automation).

1. Create an X developer project/app with access/billing sufficient for the official
   create-post endpoint and account-identity reads. Verify current eligibility in the
   developer console; no subscription tier or free quota is assumed.
2. Enable **Read and Write** user-context permissions. Authorize the **SmartBets company
   account**, not a personal account. Regenerate user tokens if permissions changed.
3. Configure the four server-only OAuth 1.0a credentials:
   `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`.
4. Set `X_EXPECTED_USER_ID` to the independently verified company account's numeric ID.
   Before POST `/2/tweets`, the client calls GET `/2/users/me` and requires an exact ID match.
5. `X_BEARER_TOKEN` is not used: app-only bearer authentication does not authorize this
   implementation to publish as a user. OAuth 2.0 PKCE is not implemented here.

All credentials are read from environment variables; never include them in tracked files,
command arguments, source snapshots, screenshots, logs, or `NEXT_PUBLIC_` variables.
`requests-oauthlib` supplies OAuth signing. Requests are bounded and do not follow redirects.

## Configuration

| Variable | Required behavior |
| --- | --- |
| `SOCIAL_DATABASE_URL` | Separate SQLite locally; durable PostgreSQL on Vercel |
| `SOCIAL_SOURCE_SIGNING_KEY` | Persistent random secret, at least 32 characters; same on authoritative host and scheduler |
| `SOCIAL_CAMPAIGN_START` | Reviewed campaign start date, ISO YYYY-MM-DD (UTC day boundary) |
| `SOCIAL_CAMPAIGN` | Optional label; default `smartbets-launch-v1` |
| `SOCIAL_CTA_URL` | Optional real HTTPS company/subscription URL; no credentials/query/fragment |
| `SOCIAL_COMPANY_DOMAIN` | Exact approved hostname for CTA, e.g. your actual company domain |
| `DRY_RUN` | Defaults true; must be explicitly `false` to post |
| `SOCIAL_AUTO_PUBLISH` | Defaults false; must be explicitly `true` to post |
| `SOCIAL_SCHEDULER_ENABLED` | Defaults false; enables cron generation when true |
| `CRON_SECRET` | Random secret for Vercel bearer authentication, at least 16 characters recommended |
| `X_EXPECTED_USER_ID` | Required company-account identity check before publishing |

Use a password manager/Vercel sensitive environment settings for secret values. Preserve
the signing key across restarts; changing it invalidates existing source/post signatures.
Scope publish credentials and enabled switches to **Production only**. Preview deployments
cannot publish even if switches are accidentally enabled. No hosting credentials were
changed or deployments triggered during implementation.

## Local dry run

Install dependencies with `py -3.14 -m pip install -r requirements.txt`. Configure the
signing key and campaign start in the environment; choose the real company URL or omit
CTA during previews. Use a separate local database:

```powershell
cd C:\Users\chris\Desktop\nbastats\nbastat
$env:SOCIAL_DATABASE_URL = 'sqlite:///C:/Users/chris/Desktop/nbastats/nbastat/social_marketing.db'
$env:DRY_RUN = 'true'
$env:SOCIAL_AUTO_PUBLISH = 'false'

py -3.14 -m backtesting.social_marketing init
py -3.14 -m backtesting.social_marketing sync-source --week-db backtesting/market_capture/nfl_2026_reg1_v1.db
py -3.14 -m backtesting.social_marketing dry-run
py -3.14 -m backtesting.social_marketing report
```

`dry-run` never invokes the publisher, even if publish switches are enabled elsewhere.
It validates, saves and prints the day's draft. `daily` generates and then invokes the
publisher gate; with defaults it remains a dry run. A dry-run demonstration during
implementation used verified data in disposable storage and printed:

> Our frozen preseason test: 11-4-1 across 16 PREDICTIONS; 0 qualified wagers. A small preseason sample, not a promise of future returns.

The demonstration was not a production campaign deployment and sent no X requests/posts.

## Explicit single-post command (do not run until approved)

After previewing a specific draft and completing all prerequisites below, explicitly set
`DRY_RUN=false` and `SOCIAL_AUTO_PUBLISH=true` in the intended environment, then:

```powershell
py -3.14 -m backtesting.social_marketing publish-one --post-id <reviewed-draft-id>
```

Both switches apply to single-post and automatic publishing. A draft must be scheduled
for today, have fresh authentic evidence, match the approved CTA/template and not already
have a daily publication claim. Old drafts are not posted later as if current.

## Daily scheduler and shutdown

The existing Vercel backend rewrites public paths to `api/index.py`; the new FastAPI route
is `/api/cron/social-daily`. Vercel supplies `Authorization: Bearer <CRON_SECRET>` and the
handler rejects absent/mismatched authorization. See [Vercel cron security](https://vercel.com/docs/cron-jobs/manage-cron-jobs).

Initialize and test the dedicated PostgreSQL ledger first. Deploy the reviewed source and
configure Production secrets, source-sync feed, campaign dates and CTA. Start with
`SOCIAL_SCHEDULER_ENABLED=true`, `DRY_RUN=true`, `SOCIAL_AUTO_PUBLISH=false`; inspect saved
drafts and cron logs. Enabling the scheduler alone does not enable posting. A local/operator
equivalent is `py -3.14 -m backtesting.social_marketing daily` once each morning.

To stop new posts immediately, set `SOCIAL_AUTO_PUBLISH=false` (or `DRY_RUN=true`) and
redeploy where environment updates require it. Also set `SOCIAL_SCHEDULER_ENABLED=false`
or disable the cron in Vercel. For an emergency, revoke the X user token. A request already
sent to X cannot be canceled by a later configuration change.

## Failure recovery

- `DRAFT`: review; fix configuration before today's publish attempt. Content/CTA changes
  require editorial review; the generator never overwrites an existing day's draft.
- `FAILED`: inspect the safe failure code (auth/account check, HTTP 401/403/429, etc.).
  Fix credentials/quota/permissions for a future day. The same day's durable claim is not
  automatically reopened; do not edit the database to force retries.
- `UNKNOWN` / stale `PUBLISHING`: delivery may have succeeded. **Never blindly repost.**
  Inspect the company account manually or via official API, then use the known remote ID:

```powershell
py -3.14 -m backtesting.social_marketing reconcile --post-id <local-id> --x-post-id <verified-remote-id>
```

This performs official API reads to verify author and exact content before marking the
ledger published; it sends no new post. `published_at` for reconciled entries is the
reconciliation timestamp, labeled by `failure_reason=RECONCILED_REMOTE_POST`, not an
invented remote creation time. If absent/uncertain, keep the claim blocked and seek review.
URLs that X shortens/rewrites may prevent exact-text reconciliation; the tool fails closed.

## Initial fourteen-day campaign

Each day has a different template, dynamically filled only from verified evidence:

| Day | Theme |
| --- | --- |
| 1 | Transparent preseason prediction record, zero wagers and small-sample caveat |
| 2 | Regular-season slate/frozen-prediction readiness |
| 3 | How prediction and wager qualification differ |
| 4 | Timestamp/provenance product features |
| 5 | Data education: accuracy is not ROI |
| 6 | Verified market coverage and honest gaps |
| 7 | Product exploration CTA |
| 8 | Model-input interpretation without invented matchup statistics |
| 9 | Responsible bankroll/uncertainty education |
| 10 | Verified regular-season results recap |
| 11 | Market movement methodology; no unsupported movement claims |
| 12 | Pregame capture/qualification process |
| 13 | Subscription/product CTA without fabricated social proof |
| 14 | Weekly evidence/transparency summary |

Guarded event draft hooks are available via `dry-run --event` with `slate_frozen`,
`pregame_picks_ready`, `sunday_recap`, and `final_weekly_results`. They require supporting
data and retain the one-post-per-day limit; no event bus or extra posting schedule is
enabled automatically. An existing daily draft wins rather than creating an extra post.

## Checklist before SOCIAL_AUTO_PUBLISH=true

1. Review/commit/deploy source; verify tests and the hosted PostgreSQL integration.
2. Configure the real company CTA/domain, campaign date, durable database and signing key.
3. Initialize the social tables using the correct dedicated role; verify access controls.
4. Verify the hourly/daily authoritative-source feed and its 36-hour freshness guard.
5. Obtain Read+Write X user credentials for the company account, verify account ID and
   current API entitlement/billing, and review X's automation/account-label requirements.
6. Configure cron secret and Production-only scheduler settings; keep previews disabled.
7. Review actual daily drafts and source references in dry-run mode, including length,
   numeric accuracy, predictions-versus-wagers wording and responsible framing.
8. Approve one specific company post, then explicitly set both publication switches.
9. Observe the first real request/remote ID, verify duplicate/error controls, and document
   how to revoke access/disable the cron. Only then leave automatic posting enabled.

## Validation status

The full backend suite passed **670 tests**, with two skips for the absent PostgreSQL
driver and the opt-in live NFL provider. Frontend **23 tests**, lint, production build
and `git diff --check` passed. Mocked X tests cover dry-run/publication switches,
company identity, official endpoints, concurrent duplicate claims, tamper/staleness
checks, uncertain delivery, read-only reconciliation, event gates, CTA allowlisting,
campaign expiry and cron authorization/error redaction. No X posting was performed.
Live PostgreSQL integration, real X account authentication, Production environment
configuration and deployment are still required before automatic publishing is approved.
