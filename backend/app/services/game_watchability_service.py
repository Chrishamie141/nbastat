"""Deterministic editorial watchability scoring; never betting advice.

Weights: scheduled base 20; national broadcast +25; primetime local/Eastern evening +15;
divisional/rivalry metadata +15 when supplied; close spread +10 and high total +8 only when
provider supplies those fields; preseason roster signals +12 when supplied. Missing facts add no points.
"""
from datetime import timezone

FORMULA = {
    'scheduledBase': 20, 'nationalBroadcast': 25, 'primeTime': 15,
    'suppliedRivalryOrDivision': 15, 'closeSpread': 10, 'highTotal': 8,
    'preseasonRosterSignal': 12, 'dataCompleteness': 7,
}
NATIONAL = {'ESPN','ABC','NBC','CBS','FOX','NFL Network','Prime Video','TNT','ESPN2'}

def score_game(game: dict) -> dict:
    score = 0; reasons=[]
    if game.get('status') == 'scheduled': score += FORMULA['scheduledBase']
    bcasts = game.get('broadcast') or []
    if game.get('nationalBroadcast') or any(str(b) in NATIONAL for b in bcasts):
        score += FORMULA['nationalBroadcast']; reasons.append('National broadcast')
    dt = game.get('startTimeUtc')
    try:
        hour = dt.astimezone(timezone.utc).hour if hasattr(dt, 'astimezone') else 0
        if 0 <= hour <= 4: score += FORMULA['primeTime']; reasons.append('Prime-time window')
    except Exception: pass
    if game.get('venue') and bcasts:
        score += FORMULA['dataCompleteness']; reasons.append('Schedule details available')
    for sig, label, weight in [('rivalry','Rivalry or divisional matchup',15),('closeSpread','Close projected spread',10),('highTotal','High projected total',8),('preseasonRosterSignal','Preseason roster competition',12)]:
        if game.get(sig): score += weight; reasons.append(label)
    if not reasons: reasons.append('Earliest relevant scheduled matchup')
    return {'watchScore': min(100, int(score)), 'watchReasons': reasons}
