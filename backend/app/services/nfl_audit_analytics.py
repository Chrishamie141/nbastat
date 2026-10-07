"""Canonical NFL prediction analytics with fail-closed market provenance."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import os
from typing import Any

from backend.app.services.nfl_experiment_service import american_profit, is_strictly_pregame
from backend.app.services.nfl_model_policy import NFL_MODEL_POLICY


SETTLED = {"WON", "LOST", "PUSH"}


def _utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _probability(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    result = result / 100 if result > 1 else result
    return result if 0 <= result <= 1 else None


def _american_implied(price: float | int) -> float:
    value = float(price)
    if value == 0:
        raise ValueError("American odds cannot be zero")
    return -value / (-value + 100) if value < 0 else 100 / (value + 100)


def canonicalize_prediction_rows(rows: list[dict]) -> dict:
    """Collapse user-scoped copies into one deterministic row per game.

    System-owned (user 0) evidence wins, followed by the production champion,
    then the earliest pregame snapshot. Conflicting copies remain visible in
    diagnostics instead of being silently treated as additional predictions.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for raw in rows:
        row = dict(raw)
        key = (int(row.get("season") or 0), str(row.get("season_type") or "regular"),
               int(row.get("week") or 0), str(row.get("game_id") or ""))
        groups[key].append(row)
    canonical, duplicate_groups, conflicts = [], [], []
    champion = NFL_MODEL_POLICY["winner"]["productionChampion"]
    for key in sorted(groups):
        copies = groups[key]
        copies.sort(key=lambda row: (
            0 if int(row.get("user_id") or 0) == 0 else 1,
            0 if row.get("model_version") == champion else 1,
            _utc(row.get("generated_at")) or datetime.max.replace(tzinfo=timezone.utc),
            int(row.get("prediction_id") or row.get("id") or 0),
        ))
        chosen = copies[0]
        canonical.append(chosen)
        if len(copies) > 1:
            duplicate_groups.append({
                "gameId": key[-1], "rowCount": len(copies),
                "canonicalPredictionId": chosen.get("prediction_id"),
                "discardedPredictionIds": [row.get("prediction_id") for row in copies[1:]],
            })
        signatures = {(
            row.get("predicted_winner"), _probability(row.get("model_probability")),
            row.get("model_version"), row.get("frozen_prediction_hash"),
        ) for row in copies}
        if len(signatures) > 1:
            conflicts.append({"gameId": key[-1], "variants": len(signatures)})
    return {
        "rows": canonical,
        "sourceRowCount": len(rows),
        "canonicalRowCount": len(canonical),
        "userScopedRowsCollapsed": len(rows) - len(canonical),
        "duplicateGroups": duplicate_groups,
        "conflicts": conflicts,
    }


def _price_for_prediction(prediction: dict, observation: dict | None) -> float | None:
    if not observation or observation.get("provider") != "the-odds-api":
        return None
    market = observation.get("market") or {}
    if not market.get("sportsbook") or not market.get("marketTimestamp"):
        return None
    if not (market.get("coverage") or {}).get("moneyline"):
        return None
    winner = prediction.get("predicted_winner")
    price = (market.get("homeOdds") if winner == prediction.get("home_team") else
             market.get("awayOdds") if winner == prediction.get("away_team") else None)
    try:
        return float(price) if price not in (None, 0) else None
    except (TypeError, ValueError):
        return None


def _valid_observation(observation: dict | None, kickoff: Any) -> bool:
    if not observation:
        return False
    market = observation.get("market") or {}
    return bool(
        observation.get("provider") == "the-odds-api"
        and market.get("sportsbook")
        and is_strictly_pregame(
            market_timestamp=observation.get("marketTimestamp") or market.get("marketTimestamp"),
            retrieved_at=observation.get("retrievedAt"), kickoff_timestamp=kickoff,
        )
    )


def weekly_prediction_metrics(*, rows: list[dict], market_histories: dict[str, dict],
                              now: datetime | None = None) -> dict:
    """Compute accuracy, calibration, market health, flat-unit ROI, and CLV.

    Entry is the first captured price no earlier than prediction generation.
    Closing is the final verified observation strictly before kickoff. Current
    or postgame prices can never enter this calculation.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    canonical = canonicalize_prediction_rows(rows)
    predictions = canonical["rows"]
    wins = sum(row.get("settlement_status") == "WON" for row in predictions)
    losses = sum(row.get("settlement_status") == "LOST" for row in predictions)
    pushes = sum(row.get("settlement_status") == "PUSH" for row in predictions)
    pending = len(predictions) - wins - losses - pushes
    decided = wins + losses
    bucket_specs = (("50.00-54.99%", .50, .55), ("55.00-59.99%", .55, .60),
                    ("60.00-64.99%", .60, .65), ("65.00%+", .65, 1.01))
    buckets = []
    brier_terms = []
    for label, low, high in bucket_specs:
        subset = [row for row in predictions if (p := _probability(row.get("model_probability"))) is not None
                  and low <= p < high and row.get("settlement_status") in SETTLED]
        bucket_wins = sum(row.get("settlement_status") == "WON" for row in subset)
        bucket_losses = sum(row.get("settlement_status") == "LOST" for row in subset)
        bucket_pushes = sum(row.get("settlement_status") == "PUSH" for row in subset)
        bucket_decided = bucket_wins + bucket_losses
        buckets.append({
            "range": label, "predictions": len(subset), "wins": bucket_wins,
            "losses": bucket_losses, "pushes": bucket_pushes,
            "averagePredictedProbability": (round(sum(_probability(row["model_probability"]) for row in subset) / len(subset), 4) if subset else None),
            "actualWinRate": round(bucket_wins / bucket_decided, 4) if bucket_decided else None,
            "accuracy": round(bucket_wins / bucket_decided * 100, 1) if bucket_decided else None,
            "sampleStatus": "REPORTABLE" if bucket_decided >= 30 else "INSUFFICIENT_SAMPLE",
        })
    for row in predictions:
        probability = _probability(row.get("model_probability"))
        if probability is not None and row.get("settlement_status") in {"WON", "LOST"}:
            brier_terms.append((probability - (1 if row["settlement_status"] == "WON" else 0)) ** 2)

    entries, closings, units, clv_values = [], [], 0.0, []
    missing, stale, rejected_postkickoff = [], [], 0
    stale_minutes = max(1, int(os.getenv("NFL_MARKET_STALE_MINUTES", "90")))
    for row in predictions:
        history = market_histories.get(str(row.get("game_id"))) or {}
        kickoff = history.get("operationalKickoff") or row.get("kickoff_time")
        first, latest, closing = history.get("first"), history.get("latest"), history.get("closing")
        rejected_postkickoff += int(history.get("rejectedPostKickoffCount") or 0)
        generation = _utc(row.get("generated_at"))
        observations = [item for item in (history.get("observations") or ([first] if first else []))
                        if _valid_observation(item, kickoff)]
        before_generation = [item for item in observations if generation is None or _utc(item.get("retrievedAt")) <= generation]
        after_generation = [item for item in observations if generation is not None and _utc(item.get("retrievedAt")) > generation]
        # Prefer the last quote known when the prediction was frozen. If the
        # price feed began later, the first subsequent pregame quote is the
        # earliest legitimate opportunity and its timestamp remains explicit.
        entry = before_generation[-1] if before_generation else after_generation[0] if after_generation else None
        entry_price = _price_for_prediction(row, entry)
        closing = closing if _valid_observation(closing, kickoff) else None
        closing_price = _price_for_prediction(row, closing)
        if entry_price is None:
            missing.append(row.get("game_id"))
        else:
            entries.append({"gameId": row.get("game_id"), "price": entry_price,
                            "timestamp": entry.get("marketTimestamp") or entry.get("retrievedAt")})
            status = row.get("settlement_status")
            if status == "WON":
                units += american_profit(entry_price)
            elif status == "LOST":
                units -= 1.0
            if status in SETTLED and closing_price is not None:
                clv_values.append(_american_implied(closing_price) - _american_implied(entry_price))
        if closing_price is not None:
            closings.append({"gameId": row.get("game_id"), "price": closing_price,
                             "timestamp": closing.get("marketTimestamp") or closing.get("retrievedAt")})
        kickoff_at = _utc(kickoff)
        latest_at = _utc((latest or {}).get("marketTimestamp") or (latest or {}).get("retrievedAt"))
        if kickoff_at and now < kickoff_at and (latest_at is None or now - latest_at > timedelta(minutes=stale_minutes)):
            stale.append(row.get("game_id"))
    graded_entries = [entry for entry in entries if next(
        (row.get("settlement_status") in SETTLED for row in predictions if row.get("game_id") == entry["gameId"]), False)]
    units_risked = len(graded_entries)
    return {
        "canonicalization": {key: value for key, value in canonical.items() if key != "rows"},
        "predictionPerformance": {
            "predictions": len(predictions), "wins": wins, "losses": losses,
            "pushes": pushes, "pending": pending,
            "accuracy": round(wins / decided * 100, 1) if decided else None,
        },
        "calibration": {
            "status": "REPORTABLE" if decided >= 30 else "INSUFFICIENT_SAMPLE",
            "sampleSize": decided, "brierScore": round(sum(brier_terms) / len(brier_terms), 6) if brier_terms else None,
            "buckets": buckets,
        },
        "marketCapture": {
            "predictionCount": len(predictions), "entryPrices": len(entries),
            "closingPrices": len(closings), "missingGameIds": sorted(set(missing)),
            "staleGameIds": sorted(set(stale)), "staleAfterMinutes": stale_minutes,
            "rejectedPostKickoffObservations": rejected_postkickoff,
        },
        "bettingPerformance": {
            "pricedSettledPredictions": units_risked, "unitsRisked": units_risked,
            "netUnits": round(units, 4) if units_risked else None,
            "roi": round(units / units_risked * 100, 2) if units_risked else None,
            "roiStatus": "AVAILABLE" if units_risked else "UNAVAILABLE_INVALID_OR_MISSING_PRICE_PROVENANCE",
            "clvSamples": len(clv_values),
            "averageClvProbabilityPoints": round(sum(clv_values) / len(clv_values) * 100, 3) if clv_values else None,
            "clvStatus": "AVAILABLE" if clv_values else "UNAVAILABLE_INVALID_OR_MISSING_CLOSING_PROVENANCE",
            "definitions": {
                "roi": "Flat one-unit net return divided by units risked; only verified pregame entry prices qualify.",
                "clv": "Closing implied probability minus entry implied probability for the predicted side; positive is favorable.",
            },
        },
    }
