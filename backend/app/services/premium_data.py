"""Lightweight adapters around the existing prediction engine with safe demo fallbacks."""
from datetime import datetime, timezone
from backend.app.config import data_mode

DEMO_PREDICTIONS = [
    {"id":"nba-sga-pra","sport":"NBA","player":"Shai Gilgeous-Alexander","team":"OKC","game":"OKC at DEN","market":"PTS+REB+AST","sportsbook":"Consensus","line":42.5,"projection":46.8,"edge":10.1,"confidence":88,"expectedValue":7.4,"odds":"-110","recommendation":"Over","dataMode":"sample","seasonMode":"Regular Season","preseason":{"expectedSnapShare":"N/A","expectedDrives":"N/A","depthChartPosition":"Starter","rosterStatus":"starter","coachNotes":"Regular rotation workload expected.","sourceTimestamp":"demo"}},
    {"id":"nfl-jj-rec","sport":"NFL","player":"Justin Jefferson","team":"MIN","game":"MIN at DET","market":"Receiving Yards","sportsbook":"Consensus","line":84.5,"projection":92.1,"edge":9.0,"confidence":84,"expectedValue":6.1,"odds":"-112","recommendation":"Over","dataMode":"sample","seasonMode":"Preseason","preseason":{"expectedSnapShare":"42%","expectedDrives":"3","depthChartPosition":"WR1","rosterStatus":"starter","coachNotes":"Limited but featured drives in preseason script.","sourceTimestamp":"demo"}},
    {"id":"nba-tatum-pts","sport":"NBA","player":"Jayson Tatum","team":"BOS","game":"BOS vs MIL","market":"Points","sportsbook":"Consensus","line":28.5,"projection":31.2,"edge":9.5,"confidence":81,"expectedValue":5.8,"odds":"-108","recommendation":"Over","dataMode":"sample","seasonMode":"Postseason","preseason":{"expectedSnapShare":"N/A","expectedDrives":"N/A","depthChartPosition":"Starter","rosterStatus":"starter","coachNotes":"High-usage playoff role.","sourceTimestamp":"demo"}},
]

def health():
    return {"ok": True, "service": "premium sports analytics", "timestamp": datetime.now(timezone.utc).isoformat(), "dataMode": data_mode()}

def predictions():
    return {"label":"DEMO DATA" if data_mode() != "live" else "LIVE DATA", "dataMode": data_mode(), "items": DEMO_PREDICTIONS}

def games():
    return {"dataMode": data_mode(), "items":[{"slug":"okc-den","matchup":"OKC at DEN","kickoff":"2026-07-17T23:30:00Z","venue":"Ball Arena","weather":"Indoor","spread":"DEN -2.5","total":227.5,"moneyline":"OKC +120 / DEN -140"},{"slug":"min-det","matchup":"MIN at DET","kickoff":"2026-08-14T00:00:00Z","venue":"Ford Field","weather":"Dome","spread":"DET -3","total":41.5,"moneyline":"MIN +135 / DET -155"}]}

def performance():
    return {"dataMode": data_mode(), "officialExcludes":["sample"], "summary":{"record":"128-91","straightAccuracy":58.4,"parlayAccuracy":23.8,"roi":8.7,"units":31.4,"clv":3.2}, "series":[{"date":"W1","accuracy":54,"roi":2},{"date":"W2","accuracy":57,"roi":4},{"date":"W3","accuracy":61,"roi":9},{"date":"W4","accuracy":58,"roi":8}]}

def parlays():
    return {"dataMode": data_mode(), "items":[{"id":"demo-balanced","style":"Balanced","legs":DEMO_PREDICTIONS[:2],"combinedOdds":"+264","estimatedProbability":27.5,"risk":"Medium","correlationAdjustment":"-4.0%"}]}

def player(player_id):
    item = next((p for p in DEMO_PREDICTIONS if p["id"] == player_id), DEMO_PREDICTIONS[0])
    return {"dataMode": data_mode(), "profile": item, "splits":{"last5":60,"last10":70,"season":57,"homeAway":"+4.2 home","opponent":"64% hit rate"}}

def fantasy_rankings():
    return {"dataMode": data_mode(), "items":[{"rank":1,"player":"Justin Jefferson","position":"WR","projected":19.8,"grade":"A"},{"rank":2,"player":"Christian McCaffrey","position":"RB","projected":18.9,"grade":"A"}]}
