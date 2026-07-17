from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.config import get_config_status, print_config_status
from backend.app.services import premium_data

print_config_status()

app = FastAPI(title="Premium Sports Analytics API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/api/health")
def health(): return premium_data.health()

@app.get("/api/config/status")
def config_status(): return {"providers": get_config_status(), "dataMode": premium_data.health()["dataMode"]}

@app.get("/api/nfl/games")
def nfl_games(): return premium_data.games()

@app.get("/api/nfl/predictions")
def nfl_predictions(): return premium_data.predictions()

@app.get("/api/nfl/predictions/{prediction_id}")
def nfl_prediction(prediction_id: str):
    return next((p for p in premium_data.predictions()["items"] if p["id"] == prediction_id), premium_data.predictions()["items"][0])

@app.post("/api/nfl/parlays/build")
def build_parlay(payload: dict):
    return {**premium_data.parlays(), "request": payload}

@app.get("/api/nfl/parlays")
def nfl_parlays(): return premium_data.parlays()

@app.get("/api/nfl/performance")
def nfl_performance(): return premium_data.performance()

@app.get("/api/players/{player_id}")
def player(player_id: str): return premium_data.player(player_id)

@app.get("/api/fantasy/rankings")
def fantasy_rankings(): return premium_data.fantasy_rankings()
