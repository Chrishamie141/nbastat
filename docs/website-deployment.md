# SmartBetSports website deployment

The website is deployed as two Vercel projects from this repository:

- `smartbetsports`: Next.js, with `frontend` as its project root.
- `smartbetsports-api`: FastAPI, with the repository root as its project root.

The frontend sends browser requests to same-origin `/api/*` paths. Its Next.js
rewrite forwards those requests to `BACKEND_API_URL`, defaulting to the stable
`https://smartbetsports-api.vercel.app` alias. This keeps authentication cookies
same-origin and prevents a production browser from calling `localhost:8000`.

The API project requires these production environment variables:

- `DATABASE_URL`
- `AUTH_SECRET`
- `AUTH_COOKIE_SECURE=true`
- `FRONTEND_ORIGIN=https://smartbetsports.vercel.app`
- `THE_ODDS_API_KEY`
- Stripe variables used by `backend/app/api/billing.py`
- `OPENWEATHER_API_KEY` (optional weather context)

The currently served NFL model is `nfl_player_prop_matchup_v2`. It uses live
pregame sportsbook props, the compact frozen 2025 recent-stat artifact, matchup,
injury, weather, consistency, and price context. The newer research policy,
`nfl_system_a_forward_shadow_v1`, remains explicitly `FROZEN_SHADOW_ONLY` and is
not described as production-wagering authorized. The API response reports both
states so the website cannot blur research validation and the deployable
prediction path.

Parlay generation and parlay-history persistence are independent. If storage is
temporarily unavailable, a valid generated prediction is still returned with a
diagnostic `saveStatus`; persistence can no longer erase the prediction result.
