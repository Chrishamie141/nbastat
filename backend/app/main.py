from datetime import datetime, timezone
from pathlib import Path
import json
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from backend.app.config import get_config_status, print_config_status
from backend.app.api.auth import router as auth_router
import os
from nfl_parlay_builder import build_nfl_parlay
from nfl_performance_report import print_nfl_performance_report
from prediction_storage import load_parlay_history, grade_recommendations, summarize_graded_bets, get_connection, initialize_database
from app import run_prediction, default_context, prediction_rows_from_result, run_roster_predictions, run_best_bets_mode, run_auto_parlay_mode
from roster_service import get_roster_with_cache
from team_utils import normalize_team_abbreviation

print_config_status()
app = FastAPI(title="SmartBetSports API", version="2.0.0")
frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
app.add_middleware(CORSMiddleware, allow_origins=[frontend_origin], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(auth_router)

def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def provider_status():
    providers=get_config_status()
    configured=[k for k,v in providers.items() if v]
    return {"configuredProviders": configured, "status": "live" if configured else "sample fallback"}
def mode(): return "live" if provider_status()["configuredProviders"] else "sample"
def envelope(sport, action, **extra): return {"sport":sport,"action":action,"generatedAt":now(),"dataMode":mode(),"providerStatus":provider_status(),"saveStatus":extra.pop("saveStatus","not saved"),**extra}
def safe_error(sport, action, exc): return envelope(sport, action, error=str(exc), providerStatus=provider_status())

@app.get("/api/health")
def health(): return {"ok": True, "generatedAt": now(), "dataMode": mode(), "providerStatus": provider_status()}
@app.get("/api/config/status")
def config_status(): return {"providers": get_config_status(), "dataMode": mode(), "providerStatus": provider_status()}

@app.get("/api/dashboard")
def dashboard():
    initialize_database(); summary={"totalPredictions":0,"gradedPredictions":0,"overallAccuracy":None,"savedParlays":0}; recent=[]
    with get_connection() as conn:
        summary["totalPredictions"]=conn.execute("SELECT COUNT(*) c FROM predictions").fetchone()["c"]
        summary["gradedPredictions"]=conn.execute("SELECT COUNT(*) c FROM graded_bets").fetchone()["c"]
        summary["savedParlays"]=conn.execute("SELECT COUNT(*) c FROM parlay_history").fetchone()["c"] if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='parlay_history'").fetchone() else 0
        hits=conn.execute("SELECT COUNT(*) c, SUM(hit) h FROM graded_bets").fetchone()
        if hits["c"]: summary["overallAccuracy"]=round((hits["h"] or 0)*100/hits["c"],1)
        rows=conn.execute("SELECT created_at, player, stat_type FROM predictions ORDER BY created_at DESC LIMIT 5").fetchall()
        recent=[{"summary":f"Prediction: {r['player']} {r['stat_type']}"} for r in rows]
    return {"summary":summary,"recent":recent,"series":[]}

@app.post("/api/analyze/nfl/parlay")
def nfl_parlay(payload: dict):
    try:
        team=payload.get("team") or None
        if team and (len(team)>3 or not team.isalpha()): raise HTTPException(400,"Use a valid NFL team abbreviation such as NYG, or leave blank.")
        result=build_nfl_parlay(payload.get("difficulty") or "BALANCED", team=team.upper() if team else None)
        legs=[leg.__dict__ for leg in result.parlay.legs]
        save="not saved"
        if legs:
            from prediction_storage import save_parlay_result
            save=f"saved #{save_parlay_result(result)}"
        return envelope("NFL","NFL Parlay Builder",legs=legs,combinedConfidence=result.combined_probability,estimatedOdds=result.estimated_odds,message=result.notes,saveStatus=save)
    except HTTPException: raise
    except Exception as exc: return safe_error("NFL","NFL Parlay Builder",exc)
@app.get("/api/analyze/nfl/history")
def nfl_history(difficulty: str|None=None): return envelope("NFL","View Parlay History",items=_history_rows("NFL", difficulty))
@app.post("/api/analyze/nfl/grade")
def nfl_grade(payload: dict): return envelope("NFL","Grade NFL Parlays",message="Select an ungraded parlay in the CLI for full grading. API grading adapter is available for submitted results.",items=[])
@app.get("/api/analyze/nfl/performance")
def nfl_perf():
    try: return envelope("NFL","View NFL Performance Report",items=[print_nfl_performance_report()])
    except Exception as exc: return safe_error("NFL","View NFL Performance Report",exc)
@app.get("/api/analyze/nfl/fantasy")
def nfl_fantasy():
    options=["Rankings", "Start/Sit Helper", "Waiver Suggestions", "Player Projection Comparison"]
    return envelope("NFL","Fantasy Football Tools",items=[{"summary":x} for x in options],message="These are the current fantasy actions exposed by the Python CLI; placeholder helper details remain unchanged.")

@app.post("/api/analyze/nba/player")
def nba_player(payload: dict):
    player=(payload.get("player") or "").strip()
    if not player: raise HTTPException(400,"Player name is required.")
    try:
        result=run_prediction(player); rows=prediction_rows_from_result(result,"Unknown",default_context(),save_to_db=True)
        return envelope("NBA","Single Player Prediction",predictions=rows,saveStatus=f"saved {len(rows)} stat rows")
    except Exception as exc: return safe_error("NBA","Single Player Prediction",exc)
@app.post("/api/analyze/nba/roster")
def nba_roster(payload: dict):
    roster_file=BASE_DIR/"roster.txt"
    if not roster_file.exists(): raise HTTPException(404,"roster.txt was not found.")
    roster=[x.strip() for x in roster_file.read_text().splitlines() if x.strip()]
    data=run_roster_predictions(roster, team="Unknown", context=default_context(), emit_output=False)
    return envelope("NBA","Default Roster Prediction",predictions=data.get("prediction_rows",[]),items=[{"player":r["player"]} for r in data.get("results",[])],saveStatus="saved generated stat rows")
@app.post("/api/analyze/nba/team")
def nba_team(payload: dict):
    team=normalize_team_abbreviation(payload.get("team"))
    if not team: raise HTTPException(400,"Team abbreviation is required.")
    _, roster, status=get_roster_with_cache(team)
    data=run_roster_predictions(roster, team=team, context=default_context(), emit_output=False) if roster else {"prediction_rows":[]}
    return envelope("NBA","Team Auto-Roster Prediction",predictions=data.get("prediction_rows",[]),message=f"Roster source: {status}",saveStatus="saved generated stat rows" if data.get("prediction_rows") else "not saved")
@app.get("/api/analyze/nba/best-bets")
def nba_best(): return envelope("NBA","Best Bets Report",message="Best bets require betting_lines.json and interactive roster context in the CLI. Use CLI for the full workflow until provider prompts are configured for API use.",items=[])
@app.post("/api/analyze/nba/parlay")
def nba_parlay(payload: dict): return envelope("NBA","Auto Parlay Builder",message="NBA auto parlay uses the existing interactive betting engine. API prompts are intentionally not invented; use CLI for complete generation until adapter inputs are configured.",items=[])
@app.post("/api/analyze/nba/grade")
def nba_grade(payload: dict):
    rows=grade_recommendations(payload.get("actualResults") or [], default_stake=float(payload.get("stake") or 10))
    return envelope("NBA","Grade Predictions",items=rows,summary=summarize_graded_bets(rows),saveStatus=f"graded {len(rows)} rows")
@app.get("/api/analyze/nba/history")
def nba_history(): return envelope("NBA","View Parlay History",items=_history_rows("NBA", None))
@app.get("/api/analyze/nba/performance")
def nba_perf(): return performance()

@app.get("/api/history")
def history(tab: str=Query("All")):
    rows=_history_rows(None,None)
    t=tab.lower()
    if t in {"nfl","nba"}: rows=[r for r in rows if r["sport"].lower()==t]
    if t=="graded": rows=[r for r in rows if r["resultStatus"]!="pending"]
    if t=="ungraded": rows=[r for r in rows if r["resultStatus"]=="pending"]
    if t=="predictions": rows=[]
    if t=="parlays": rows=rows
    return {"items":rows[:50]}
@app.get("/api/performance")
def performance(): return {"metrics":_metrics(),"series":[]}

def _history_rows(sport=None,difficulty=None):
    rows=load_parlay_history(sport=sport,difficulty=difficulty)
    out=[]
    for r in rows:
        legs=json.loads(r.get("legs_json") or "[]")
        out.append({"date":r.get("created_at"),"sport":r.get("sport"),"action":"Parlay","summary":f"{r.get('difficulty')} parlay with {len(legs)} legs","resultStatus":r.get("result_status"),"dataMode":mode()})
    return out

def _metrics():
    initialize_database(); m={}
    with get_connection() as conn:
        row=conn.execute("SELECT COUNT(*) c, SUM(hit) h FROM graded_bets").fetchone()
        if row["c"]: m["Overall graded prediction accuracy"]=f"{round((row['h'] or 0)*100/row['c'],1)}%"
    return m
