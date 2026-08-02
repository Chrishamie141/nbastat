# APEX EDGE Premium Sports Analytics

A premium dark sports-analytics website wrapped around the existing NBA/NFL Python prediction engine. The original CLI prediction, roster, grading, fantasy, parlay, cache, and performance modules remain in place while a FastAPI adapter and Next.js JSX frontend provide a modern product shell.

## What is included

- Existing Python prediction engine and all current NBA/NFL workflows are preserved.
- Safe environment loading with `python-dotenv` happens before services are imported.
- FastAPI backend endpoints expose health, config status, NFL games, predictions, parlays, performance, players, and fantasy rankings.
- Next.js App Router frontend using JSX, Tailwind CSS, Framer Motion, Lenis, Lucide React, and Recharts.
- Premium dark glassmorphism UI with CSS variables, reduced-motion support, responsive layouts, inline SVG/CSS effects, and no committed binary assets.
- Explicit data-mode labels: `sample`, `partial_live`, and `live`. Sample data is displayed as `DEMO / SAMPLE DATA — NOT FOR LIVE WAGERING` and is excluded from official performance messaging.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a root `.env` file when live providers are available:

```bash
THE_ODDS_API_KEY=...
OPENWEATHER_API_KEY=...
```

Missing keys do not crash startup. The app prints a safe status table that never includes secret values and falls back to demo/sample data when providers are unavailable.

## Run the existing Python CLI

```bash
python app.py --health-check
python app.py
```

## Run the FastAPI backend

```bash
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Useful endpoints:

- `GET /api/health`
- `GET /api/config/status`
- `GET /api/nfl/games`
- `GET /api/nfl/predictions`
- `GET /api/nfl/predictions/{id}`
- `POST /api/nfl/parlays/build`
- `GET /api/nfl/parlays`
- `GET /api/nfl/performance`
- `GET /api/players/{id}`
- `GET /api/fantasy/rankings`

## Run the frontend

```bash
cd frontend
npm install
npm run dev
```

Set `NEXT_PUBLIC_API_URL=http://localhost:8000` if the backend is not on the default URL.

Production checks:

```bash
cd frontend
npm run lint
npm run build
```

## Binary asset policy

Binary/local runtime files are intentionally excluded. Do not commit PNG, JPG, GIF, WEBP, ICO, video, audio, font binaries, ZIPs, compiled files, SQLite databases, or downloaded fonts. Visual effects should be built with CSS gradients, Tailwind, JSX, inline SVG, Lucide icons, Framer Motion, Lenis, and Recharts.


## Frontend workflow and development authentication

The simplified frontend is organized around the original `app.py` CLI decision tree: landing page → local demo login/register → dashboard → `/analyze` → sport → CLI action → options → result.

Authentication is development-only. `frontend/components/auth/AuthProvider.jsx` stores a basic demo session in `localStorage` and exposes `login`, `register`, and `logout` actions behind a `useAuth` hook so it can later be replaced by Auth.js, Clerk, Supabase Auth, or another provider. Do not use this local demo authentication for production access control.

Protected frontend routes redirect unauthenticated users to `/login`. No real credentials are hardcoded.

## Team logo source

The web dashboard uses remote team logo URLs from ESPN's stable team-logo CDN (`https://a.espncdn.com/i/teamlogos/...`) through the centralized backend team metadata in `backend/app/services/team_metadata.py`. Logos are not downloaded during page rendering and no local binary logo assets are committed. If ESPN logo terms change, replace the URL mapping with another approved official or stable provider before shipping.


## NFL data providers

SmartBetSports NFL uses The Odds API, ESPN, optional verified NFL data, OpenWeather, and local JSON exports. The Odds API (`THE_ODDS_API_KEY`) supplies NFL moneyline, spread, total, player-prop, bookmaker, and historical odds data where the configured subscription supports it. ESPN endpoints supply NFL schedules, event IDs, start times, teams, final scores, rosters/box scores, and available player/team statistics. The optional NFL official adapter is disabled unless a dependable NFL-hosted JSON endpoint is verified; it is supplemental only. OpenWeather (`OPENWEATHER_API_KEY`) remains the weather source. Local JSON exports can fill historical gaps; current odds must not be relabeled as historical point-in-time odds.

## NFL player-prop error analysis

After generating the season player-prop evaluation artifacts, run the deterministic offline error analysis:

```bash
python -m backtesting.analyze_nfl_player_prop_errors --season 2025 --start-week 1 --end-week 18 --snapshot-root backtesting/data/snapshots --season-results-dir backtesting/results/nfl_player_props_2025_history --output-dir backtesting/results/nfl_player_props_2025_error_analysis --top-n 50 --min-segment-size 20
```

The command attributes extreme probabilities to persisted distribution geometry and history-depth fields, measures market overconfidence, ranks team and market-role archetype errors, separates mean-bias from variance-underestimation signals, and identifies positive-edge opportunities contributing most to realized ROI loss. It never contacts a provider or changes predictions. Attribution is observational because raw simulator feature vectors and causal coefficients are not persisted.

Primary outputs are `error_analysis_summary.json`, `feature_attribution.json`, `market_overconfidence.json`, `segment_metrics.json`, `mean_variance_diagnostics.json`, `roi_loss_contributors.json`, their CSV counterparts, and `analysis_manifest.json`.

For market-family comparison and leakage-safe modeling research, run:

```bash
python -m backtesting.research_nfl_player_prop_models --season 2025 --start-week 1 --end-week 18 --snapshot-root backtesting/data/snapshots --season-results-dir backtesting/results/nfl_player_props_2025_history --output-dir backtesting/results/nfl_player_props_2025_model_research --model-id nfl_game_baseline_v3 --simulations 10000 --seed 1729 --min-train-rows 100 --min-test-rows 20 --min-segment-size 20
```

This compares Normal, Lognormal, Gamma, Poisson, Negative Binomial, zero-inflated Poisson, and zero-inflated Negative Binomial forecasts by market. It also supports expanding walk-forward variance models, isotonic and beta calibration, permutation importance, and residual clusters for team, player archetype, sportsbook, line size, favorite/underdog, home/away, implied team total, and projected pace. Learned stages fail closed as `INSUFFICIENT_HISTORY` until each test fold has at least two prior evaluated weeks. Research outputs never mutate production predictions.

## Immutable model registry

Every model and experiment has an append-only, content-addressed record under `backtesting/model_registry`. The research command writes `experiment_result.json` using the shared v1 contract in `backtesting/model_registry/experiment_result.schema.json`. It includes Git, configuration, and input-dataset hashes; train/evaluation windows; reproducibility settings; Brier score, log loss, ECE, and ROI with game-cluster uncertainty; calibration bins; a reliability plot; and profit/quality breakdowns by market and confidence bucket.

Register and validate an experiment explicitly:

```bash
python -m backtesting.model_registry --root backtesting/model_registry register-experiment --result backtesting/results/nfl_player_props_2025_model_research/experiment_result.json
python -m backtesting.model_registry --root backtesting/model_registry validate
python -m backtesting.model_registry --root backtesting/model_registry promotion-check --experiment-id nfl_game_baseline_v3.2025.w01-w18.a4d5378e021f --baseline-model-id nfl_game_baseline_v3
```

Registration is idempotent only when content is identical; reusing an ID for different content fails. The registry index is derived and hash-validated. Promotion fails closed unless evidence is leakage-safe, out-of-sample, paired against the benchmark on identical opportunities, spans at least 15 evaluated weeks and 100 independent games, and the 95% confidence intervals show lower Brier/log loss and higher ROI without worse ECE. The current Week 1 baseline is registered as `INSUFFICIENT_HISTORY`, so it cannot be promoted.

## NFL Player Prop V4 research candidate

V4 replaces the history-distribution-first player simulator with per-market supervised models. It uses 2024 completed games to build weekly leakage-safe features, fits an equal-weight Elastic Net/Random Forest/histogram-gradient-boosting ensemble for each conditional mean, learns variance from squared walk-forward residuals, selects a distribution backend by walk-forward negative log likelihood, and evaluates on frozen 2025 prop opportunities. Every player-market projection includes local feature-ablation explanations; aggregate permutation importance is recorded by fold. Research-only expected value and quarter-Kelly sizing are capped at 5%.

```bash
python -m backtesting.research_nfl_player_prop_v4 --snapshot-root backtesting/data/snapshots --season-results-dir backtesting/results/nfl_player_props_2025_history --output-dir backtesting/results/nfl_player_props_v4_2025_research --evaluation-season 2025 --start-week 1 --end-week 18 --training-seasons 2024 --seed 1729 --simulations 10000 --min-train-rows 100 --kelly-fraction 0.25 --kelly-cap 0.05 --registry-root backtesting/model_registry --register
```

The command writes training metrics, distribution selection, calibration status, permutation importance, feature coverage, compact opportunity predictions, deduplicated explained player-market projections, a reliability plot, paired baseline deltas with game-cluster confidence intervals, an experiment contract, and a deterministic manifest. It is offline and does not alter production predictions.

Distribution resolution is registry-backed and fails closed for unpromoted models. Research callers must opt in explicitly:

```bash
python -m backtesting.model_registry --root backtesting/model_registry best-distribution --market receiving_yards --model-id nfl_prop_v4_research_v1 --allow-experimental
```

The current real run trains on 11,880 samples and evaluates 1,836 Week 1 opportunities. Its Brier, log-loss, and ECE improvements over `nfl_game_baseline_v3` clear zero in the paired 95% intervals, but its ROI interval crosses zero. Calibration remains `INSUFFICIENT_HISTORY` because only one evaluated prop week exists. The registry therefore keeps `nfl_prop_v4_research_v1` experimental and rejects promotion until at least 15 evaluated weeks, 100 independent games, and statistically positive ROI evidence exist.
