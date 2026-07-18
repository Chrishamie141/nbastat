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
SPORTSDATAIO_API_KEY=...
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
