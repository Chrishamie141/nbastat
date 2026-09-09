# NFL active-season server automation

## Decision

Production uses Supabase Cron (`pg_cron`) plus `pg_net` to call a bounded Vercel REST
endpoint every five minutes. Vercel Hobby cron is limited to daily schedules, and a
long-running local process depends on one PC remaining awake. The REST endpoint is not
the scheduler: it performs one idempotent tick and exits.

## Safety boundaries

- Active context resolves across preseason, regular season, and postseason, then remains
  pinned in PostgreSQL until the slate has ended. Offseason ticks are idle.
- A tick makes at most one league-wide The Odds API request and only when a checkpoint is due.
- Each paid request reserves three credits in a committed transaction before network I/O.
  An interrupted request is not replayed automatically because its billing outcome is unknown.
- A PostgreSQL advisory transaction lock, durable request keys, and checkpoint state make
  duplicate/concurrent scheduler delivery benign.
- PostgreSQL triggers reject market evidence at or after the checkpoint deadline or kickoff,
  and prevent updates/deletes to captured markets and quotes.
- T−24h, T−6h, and T−60m checkpoints prioritize broad game coverage. A weekly ceiling and
  six-credit account reserve fail closed on quota, missing auth, changed provider cost,
  missing usage headers, rate limits, malformed data, or network uncertainty.
- Tables have RLS enabled and no browser policies. `anon` and `authenticated` have no access.
- The audited 2026 preseason Week 3 experiment is explicitly excluded.
- The service does not train/tune models, change frozen predictions, or publish social content.

## Configuration

Configure these as Production-only Vercel variables:

| Variable | Purpose |
| --- | --- |
| `NFL_AUTOMATION_ENABLED=true` | Master server-side enable switch |
| `NFL_AUTOMATION_SECRET` | Random bearer secret of at least 32 characters |
| `NFL_AUTOMATION_BASE_URL=https://smartbetsports-api.vercel.app` | Canonical API origin |
| `NFL_AUTOMATION_CREDITS_PER_WEEK=90` | Hard per-slate reservation ceiling |
| `THE_ODDS_API_KEY` | Existing server-only provider credential |

The install endpoint stores only the dedicated bearer in Supabase Vault, then creates or
updates the named `smartbets-nfl-active-season` cron job. The cron definition reads the
secret from Vault at execution time; it does not contain plaintext credentials.

## Endpoints

- `POST /api/cron/nfl-active-season` — scheduler tick; dedicated bearer required.
- `GET /api/cron/nfl-active-season/status` — protected scheduler/state report.
- `POST /api/cron/nfl-active-season/install` — idempotent Vault/cron installation.
- `GET /api/internal/operations/nfl-automation` — owner-authenticated operational report.

Do not call the tick endpoint merely to test provider connectivity: it will contact the
paid provider when and only when a checkpoint is due. The status endpoints are read-only.

## Failure recovery

Inspect the protected status before intervening. `AUTH_ERROR`, `QUOTA_EXHAUSTED`,
`COST_CHANGED`, and `QUOTA_UNKNOWN` are fail-closed states. Never backfill a missed
pregame capture with a current or post-kickoff line. Resolve the credential/quota issue,
record the operator action, and allow only future checkpoints to proceed.

Supabase exposes scheduler execution history in `cron.job_run_details`. The protected
status response includes the latest run without revealing the Vault secret. Local Windows
Task Scheduler jobs remain disabled and are not part of production operation.
