from datetime import datetime, timezone
from pathlib import Path
import json
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

from fastapi import FastAPI, HTTPException, Query, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from backend.app.config import get_config_status, print_config_status
from backend.app.api.auth import router as auth_router
from backend.app.api.billing import router as billing_router
from backend.app.services.entitlement_service import require_full_access
from backend.app.services.auth_service import current_user
from backend.app.services.sports_mode_service import get_sports_mode
from backend.app.services.schedule_service import upcoming_games
from backend.app.services.team_metadata import teams_for_league
from backend.app.schemas.common import DashboardMetrics, FeaturedGame
import os
from nfl_parlay_builder import build_nfl_parlay
from nfl_performance_report import print_nfl_performance_report
from prediction_storage import load_parlay_history, grade_recommendations, summarize_graded_bets, get_connection, initialize_database, ensure_user_columns
from app import run_prediction, default_context, prediction_rows_from_result, run_roster_predictions, run_best_bets_mode, run_auto_parlay_mode
from roster_service import get_roster_with_cache
from team_utils import normalize_team_abbreviation

print_config_status()
app = FastAPI(title="SmartBetSports API", version="2.0.0")
frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
app.add_middleware(CORSMiddleware, allow_origins=[frontend_origin], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(auth_router)
app.include_router(billing_router)

def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def provider_status():
    providers=get_config_status()
    configured=[k for k,v in providers.items() if v == "Loaded"]
    return {"configuredProviders": configured, "status": "live" if configured else "sample"}
def mode(): return "live" if provider_status()["configuredProviders"] else "sample"
def envelope(sport, action, **extra): return {"sport":sport,"action":action,"generatedAt":now(),"dataMode":mode(),"providerStatus":provider_status(),"saveStatus":extra.pop("saveStatus","not saved"),**extra}
def safe_error(sport, action, exc): return envelope(sport, action, error=str(exc), providerStatus=provider_status())

@app.get("/api/health")
def health(): return {"ok": True, "generatedAt": now(), "dataMode": mode(), "providerStatus": provider_status()}
@app.get("/api/config/status")
def config_status(): return {"providers": get_config_status(), "dataMode": mode(), "providerStatus": provider_status()}

@app.get("/api/sports-mode")
def sports_mode():
    return get_sports_mode().model_dump(mode="json")


@app.get("/api/teams")
def api_teams(league: str=Query(..., pattern="^(nfl|nba)$")):
    return {"teams":[t.model_dump(mode="json") for t in teams_for_league(league)]}

@app.get("/api/games/upcoming")
def api_upcoming_games(league: str|None=None, limit: int=Query(8, ge=1, le=20)):
    sm=get_sports_mode(); leagues=[league.lower()] if league else sm.activeLeagues
    leagues=[l for l in leagues if l in sm.activeLeagues and l in {"nfl","nba"}]
    games=upcoming_games(leagues, limit=limit) if leagues else []
    return {"items":[g.model_dump(mode="json") for g in games],"sportsMode":sm.model_dump(mode="json"),"lastUpdated":now(),"source": games[0].dataProvider if games else sm.source}

@app.get("/api/games/featured")
def api_featured_game():
    sm=get_sports_mode(); games=upcoming_games(sm.activeLeagues, limit=20) if sm.activeLeagues else []
    featured=max(games, key=lambda g:(g.watchScore, -g.startTimeUtc.timestamp()), default=None)
    return FeaturedGame(game=featured, reason="Highest deterministic watch-interest score from upcoming schedule signals." if featured else "No upcoming supported games are available.").model_dump(mode="json")

@app.get("/api/dashboard")
def dashboard(request: Request, user=Depends(require_full_access)):
    user=current_user(request); initialize_database(); ensure_user_columns();
    definitions={"savedAnalyses":"Account-owned saved analysis records, excluding legacy rows without user_id.","individualPredictions":"Account-owned rows in predictions; parlay legs are counted separately only when saved as predictions.","gradedPredictions":"Account-owned graded_bets rows.","savedParlays":"Account-owned saved rows in parlay_history.","overallAccuracy":"Hit rate across account-owned graded predictions; hidden until at least five graded rows exist."}
    recent=[]
    with get_connection() as conn:
        uid=user['id']
        pred=conn.execute("SELECT COUNT(*) c FROM predictions WHERE user_id=?",(uid,)).fetchone()["c"]
        graded_row=conn.execute("SELECT COUNT(*) c, SUM(hit) h FROM graded_bets WHERE user_id=?",(uid,)).fetchone()
        parlays=conn.execute("SELECT COUNT(*) c FROM parlay_history WHERE user_id=?",(uid,)).fetchone()["c"] if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='parlay_history'").fetchone() else 0
        acc=round((graded_row['h'] or 0)*100/graded_row['c'],1) if graded_row['c'] and graded_row['c']>=5 else None
        metrics=DashboardMetrics(savedAnalyses=pred+parlays,individualPredictions=pred,gradedPredictions=graded_row['c'],savedParlays=parlays,overallAccuracy=acc,definitions=definitions)
        rows=conn.execute("SELECT created_at, player, stat_type FROM predictions WHERE user_id=? ORDER BY created_at DESC LIMIT 5",(uid,)).fetchall()
        recent=[{"summary":f"Prediction: {r['player']} {r['stat_type']}"} for r in rows]
    sm=get_sports_mode(); errors={}; games=[]
    if sm.activeLeagues:
        try:
            games=upcoming_games(sm.activeLeagues, limit=8)
        except Exception:
            errors['upcomingGames']='Upcoming schedule unavailable'
    featured=max(games, key=lambda g:(g.watchScore, -g.startTimeUtc.timestamp()), default=None)
    if sm.activeLeagues and not featured and errors.get('upcomingGames'):
        errors['featuredGame']='Featured game unavailable'
    return {"summary":metrics.model_dump(mode="json"),"sportsMode":sm.model_dump(mode="json"),"featuredGame":featured.model_dump(mode="json") if featured else None,"upcomingGames":[g.model_dump(mode="json") for g in games],"errors":errors,"recent":recent,"series":[]}

@app.post("/api/analyze/nfl/parlay")
def nfl_parlay(payload: dict, user=Depends(require_full_access)):
    try:
        team=payload.get("team") or None
        if team and (len(team)>3 or not team.isalpha()): raise HTTPException(400,"Use a valid NFL team abbreviation such as NYG, or leave blank.")
        result=build_nfl_parlay(payload.get("difficulty") or "BALANCED", team=team.upper() if team else None)
        legs=[leg.__dict__ for leg in result.parlay.legs]
        save="not saved"
        if legs:
            from prediction_storage import save_parlay_result
            save=f"saved #{save_parlay_result(result)}"
        sm=get_sports_mode(); phase=sm.phaseByLeague.get("nfl","regular_season")
        return envelope("NFL","NFL Parlay Builder",league="nfl",seasonPhase=phase,confidenceContext=("NFL preseason projections carry extra playing-time and roster uncertainty; prop coverage may be limited." if phase=="preseason" else "Regular-season confidence context from established prediction engine."),legs=legs,combinedConfidence=result.combined_probability,estimatedOdds=result.estimated_odds,message=result.notes,saveStatus=save)
    except HTTPException: raise
    except Exception as exc: return safe_error("NFL","NFL Parlay Builder",exc)
@app.get("/api/analyze/nfl/history")
def nfl_history(difficulty: str|None=None, user=Depends(require_full_access)): return envelope("NFL","View Parlay History",items=_history_rows("NFL", difficulty))
@app.post("/api/analyze/nfl/grade")
def nfl_grade(payload: dict, user=Depends(require_full_access)): return envelope("NFL","Grade NFL Parlays",message="Select an ungraded parlay in the CLI for full grading. API grading adapter is available for submitted results.",items=[])
@app.get("/api/analyze/nfl/performance")
def nfl_perf(user=Depends(require_full_access)):
    try: return envelope("NFL","View NFL Performance Report",items=[print_nfl_performance_report()])
    except Exception as exc: return safe_error("NFL","View NFL Performance Report",exc)
@app.get("/api/analyze/nfl/fantasy")
def nfl_fantasy(user=Depends(require_full_access)):
    options=["Rankings", "Start/Sit Helper", "Waiver Suggestions", "Player Projection Comparison"]
    sm=get_sports_mode(); phase=sm.phaseByLeague.get("nfl","regular_season")
    return envelope("NFL","Fantasy Football Tools",league="nfl",seasonPhase=phase,confidenceContext=("NFL preseason context: depth-chart uncertainty and coach announcements matter when available." if phase=="preseason" else "Regular-season context."),items=[{"summary":x} for x in options],message="These are the current fantasy actions exposed by the Python CLI; placeholder helper details remain unchanged.")

@app.post("/api/analyze/nba/player")
def nba_player(payload: dict, user=Depends(require_full_access)):
    player=(payload.get("player") or "").strip()
    if not player: raise HTTPException(400,"Player name is required.")
    try:
        result=run_prediction(player); rows=prediction_rows_from_result(result,"Unknown",default_context(),save_to_db=True)
        return envelope("NBA","Single Player Prediction",predictions=rows,saveStatus=f"saved {len(rows)} stat rows")
    except Exception as exc: return safe_error("NBA","Single Player Prediction",exc)
@app.post("/api/analyze/nba/roster")
def nba_roster(payload: dict, user=Depends(require_full_access)):
    roster_file=BASE_DIR/"roster.txt"
    if not roster_file.exists(): raise HTTPException(404,"roster.txt was not found.")
    roster=[x.strip() for x in roster_file.read_text().splitlines() if x.strip()]
    data=run_roster_predictions(roster, team="Unknown", context=default_context(), emit_output=False)
    return envelope("NBA","Default Roster Prediction",predictions=data.get("prediction_rows",[]),items=[{"player":r["player"]} for r in data.get("results",[])],saveStatus="saved generated stat rows")
@app.post("/api/analyze/nba/team")
def nba_team(payload: dict, user=Depends(require_full_access)):
    team=normalize_team_abbreviation(payload.get("team"))
    if not team: raise HTTPException(400,"Team abbreviation is required.")
    _, roster, status=get_roster_with_cache(team)
    data=run_roster_predictions(roster, team=team, context=default_context(), emit_output=False) if roster else {"prediction_rows":[]}
    return envelope("NBA","Team Auto-Roster Prediction",predictions=data.get("prediction_rows",[]),message=f"Roster source: {status}",saveStatus="saved generated stat rows" if data.get("prediction_rows") else "not saved")
@app.get("/api/analyze/nba/best-bets")
def nba_best(user=Depends(require_full_access)): return envelope("NBA","Best Bets Report",message="Best bets require betting_lines.json and interactive roster context in the CLI. Use CLI for the full workflow until provider prompts are configured for API use.",items=[])
@app.post("/api/analyze/nba/parlay")
def nba_parlay(payload: dict, user=Depends(require_full_access)): return envelope("NBA","Auto Parlay Builder",message="NBA auto parlay uses the existing interactive betting engine. API prompts are intentionally not invented; use CLI for complete generation until adapter inputs are configured.",items=[])
@app.post("/api/analyze/nba/grade")
def nba_grade(payload: dict, user=Depends(require_full_access)):
    rows=grade_recommendations(payload.get("actualResults") or [], default_stake=float(payload.get("stake") or 10))
    return envelope("NBA","Grade Predictions",items=rows,summary=summarize_graded_bets(rows),saveStatus=f"graded {len(rows)} rows")
@app.get("/api/analyze/nba/history")
def nba_history(user=Depends(require_full_access)): return envelope("NBA","View Parlay History",items=_history_rows("NBA", None))
@app.get("/api/analyze/nba/performance")
def nba_perf(user=Depends(require_full_access)): return performance()

@app.get("/api/history")
def history(tab: str=Query("All"), user=Depends(require_full_access)):
    rows=_history_rows(None,None)
    t=tab.lower()
    if t in {"nfl","nba"}: rows=[r for r in rows if r["sport"].lower()==t]
    if t=="graded": rows=[r for r in rows if r["resultStatus"]!="pending"]
    if t=="ungraded": rows=[r for r in rows if r["resultStatus"]=="pending"]
    if t=="predictions": rows=[]
    if t=="parlays": rows=rows
    return {"items":rows[:50]}
@app.get("/api/performance")
def performance(user=Depends(require_full_access)): return {"metrics":_metrics(),"series":[]}

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
