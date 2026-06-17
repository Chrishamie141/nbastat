"""NFL parlay builder flow using starter placeholder projections."""

from models import DifficultyLevel, Parlay, ParlayLeg, ParlayResult, SportType
from nfl_data_service import get_nfl_lines
from nfl_predictor import NFLPredictor
from prediction_storage import save_parlay_result

DIFFICULTY_RULES = {
    DifficultyLevel.SAFE: {"legs": 3, "min_confidence": 62},
    DifficultyLevel.BALANCED: {"legs": 5, "min_confidence": 56},
    DifficultyLevel.AGGRESSIVE: {"legs": 7, "min_confidence": 50},
}


def _prediction_to_leg(prediction, nfl_lines):
    player_lines = nfl_lines.get(prediction.player, {})
    stat_lines = player_lines.get(prediction.stat_type, [])
    line_info = stat_lines[0] if stat_lines else {}
    line = line_info.get("line")
    odds = line_info.get("odds")
    if line is None:
        pick = f"{prediction.player} {prediction.stat_type}: {prediction.prediction:.1f} projected"
    else:
        pick = f"{prediction.player} over {line:g} {prediction.stat_type}"
    return ParlayLeg(
        sport=SportType.NFL,
        player=prediction.player,
        team=prediction.team,
        stat_type=prediction.stat_type,
        line=line,
        odds=odds,
        prediction=pick,
        confidence=prediction.confidence,
        notes=prediction.notes,
    )


def build_nfl_parlay(difficulty, team=None):
    difficulty = DifficultyLevel.from_input(difficulty)
    rules = DIFFICULTY_RULES[difficulty]
    predictor = NFLPredictor()
    nfl_lines = get_nfl_lines()
    projections = predictor.predict_player_pool(team=team)
    candidates = [p for p in projections if p.confidence >= rules["min_confidence"]]
    candidates.sort(key=lambda row: (row.confidence, row.prediction), reverse=True)
    legs = [_prediction_to_leg(prediction, nfl_lines) for prediction in candidates[: rules["legs"]]]
    parlay = Parlay(
        sport=SportType.NFL,
        difficulty=difficulty,
        legs=legs,
        notes="NFL starter parlay built with placeholder projections; replace data service with live NFL stats/odds.",
    )
    combined_probability = 1.0
    for leg in legs:
        combined_probability *= max(min(leg.confidence / 100, 0.95), 0.01)
    estimated_odds = None
    if all(leg.odds is not None for leg in legs) and legs:
        # Placeholder odds handling: use confidence-derived probability until a shared odds service is added.
        estimated_odds = round((1 / max(combined_probability, 0.01) - 1) * 100)
    return ParlayResult(
        parlay=parlay,
        estimated_odds=estimated_odds,
        combined_probability=combined_probability if legs else 0,
        notes=parlay.notes,
    )


def print_nfl_parlay_result(result):
    print("\n========================")
    print(f"NFL {result.parlay.difficulty.value} PARLAY")
    print("========================")
    if not result.parlay.legs:
        print("No NFL legs found. Add real NFL data/odds or broaden the selected team/player pool.")
        return
    for index, leg in enumerate(result.parlay.legs, 1):
        odds_label = f" ({leg.odds:+d})" if leg.odds is not None else ""
        line_label = f" | Line: {leg.line}" if leg.line is not None else " | No live line"
        print(f"{index}. {leg.prediction}{odds_label}{line_label} | Confidence: {leg.confidence:.0f}%")
        print(f"   {leg.notes}")
    if result.estimated_odds is not None:
        print(f"\nEstimated placeholder odds: {result.estimated_odds:+d}")
    print(f"Combined confidence proxy: {result.combined_probability * 100:.1f}%")
    print(result.notes)


def run_nfl_parlay_flow():
    print("\nNFL Parlay Builder")
    print("1. Safe")
    print("2. Balanced")
    print("3. Aggressive")
    difficulty = input("Choose difficulty 1, 2, or 3: ").strip() or "2"
    team = input("Optional NFL team abbreviation filter (press Enter for sample pool): ").strip().upper() or None
    try:
        result = build_nfl_parlay(difficulty, team=team)
    except ValueError as exc:
        print(exc)
        return None
    print_nfl_parlay_result(result)
    parlay_id = save_parlay_result(result)
    print(f"Saved NFL parlay history row #{parlay_id} to predictions.db.")
    return result
