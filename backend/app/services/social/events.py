"""Derive immutable, evidence-linked social events from verified source snapshots."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from .models import EVENT_TYPES
from .storage import json_value


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Social evidence timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _event(event_type: str, source_id: str, evidence: dict[str, Any], *, game: dict | None = None,
           at: datetime) -> dict[str, Any]:
    if event_type not in EVENT_TYPES:
        raise ValueError("Unsupported social event type")
    game = game or {}
    prediction = game.get("prediction") or {}
    market = game.get("market") or {}
    grade = game.get("grade") or {}
    return {
        "event_type": event_type,
        "game_id": game.get("game_id"),
        "source_id": source_id,
        "prediction_id": prediction.get("prediction_hash") or prediction.get("artifact_hash"),
        "model_version": prediction.get("model_version"),
        "model_hash": prediction.get("model_hash"),
        "market_snapshot_id": market.get("snapshot_id") or market.get("capture_ref"),
        "grade_evidence_id": grade.get("grade_id") or grade.get("graded_at"),
        "event_at": at.isoformat(),
        "evidence": evidence,
    }


def discover(source_id: str, source: dict[str, Any], clock) -> list[dict[str, Any]]:
    """Return only events supported by contemporaneous verified evidence."""
    at = clock().astimezone(timezone.utc)
    games = list((source.get("operations") or {}).get("games") or [])
    events: list[dict[str, Any]] = []
    predictions = [game for game in games if game.get("prediction")]
    if predictions:
        events.append(_event("SLATE_READY", source_id, {
            "games_count": len(games), "predictions_count": len(predictions),
            "source_timestamp": source.get("verified_at"),
        }, at=at))

    day_final_count = 0
    graded_sequence: list[tuple[datetime, str]] = []
    for game in games:
        kickoff = _dt(game.get("kickoff_time"))
        prediction = game.get("prediction") or {}
        generated_at = _dt(prediction.get("generated_at"))
        market = game.get("market") or {}
        final = game.get("final") or {}
        grade = game.get("grade") or {}
        base = {
            "game_id": game.get("game_id"), "away_team": game.get("away_team"),
            "home_team": game.get("home_team"), "kickoff_time": game.get("kickoff_time"),
            "prediction": prediction, "market": market, "final": final, "grade": grade,
            "source_timestamp": source.get("verified_at"),
        }
        pregame = bool(kickoff and at < kickoff and prediction and generated_at and generated_at < kickoff)
        if pregame:
            events.append(_event("PREDICTION_READY", source_id, base, game=game, at=generated_at))
            probability = float(prediction.get("probability") or 0)
            if probability >= .65:
                events.append(_event("HIGH_CONFIDENCE_PREDICTION", source_id, base, game=game, at=generated_at))
            if kickoff - at <= timedelta(hours=2):
                events.append(_event("GAME_STARTING_SOON", source_id, base, game=game, at=at))
            local_hour = kickoff.astimezone(ZoneInfo("America/New_York")).hour
            if local_hour >= 20:
                events.append(_event("PRIMETIME_GAME", source_id, base, game=game, at=at))
            market_probability = market.get("probability")
            edge = market.get("edge")
            if market_probability is not None and edge is not None and market.get("snapshot_id"):
                if abs(float(edge)) >= .05:
                    events.append(_event("MODEL_MARKET_DISAGREEMENT", source_id, base, game=game, at=at))
                if market.get("movement") is not None and abs(float(market["movement"])) >= .03:
                    events.append(_event("LINE_MOVEMENT", source_id, base, game=game, at=at))
                if prediction.get("winner") == market.get("underdog"):
                    events.append(_event("UPSET_WATCH", source_id, base, game=game, at=at))

        # A receipt is impossible without both a frozen pregame prediction and
        # persisted grade/final evidence.  Final data never creates a pregame event.
        result = str(grade.get("result") or "").upper()
        if final and result in {"WIN", "LOSS", "PUSH"} and generated_at and kickoff and generated_at < kickoff:
            day_final_count += 1
            final_at = _dt(final.get("retrieved_at") or grade.get("graded_at")) or at
            events.append(_event("GAME_FINAL", source_id, base, game=game, at=final_at))
            events.append(_event(f"PREDICTION_{result}", source_id, base, game=game, at=final_at))
            graded_sequence.append((final_at, result))

    regular = source.get("regular") or {}
    if day_final_count:
        events.append(_event("DAILY_SLATE_COMPLETE", source_id, {
            "finals_count": day_final_count, "record": regular.get("winner_record"),
            "source_timestamp": source.get("verified_at"),
        }, at=at))
    if games and regular.get("graded") == regular.get("scheduled") and regular.get("scheduled"):
        events.append(_event("WEEKLY_SLATE_COMPLETE", source_id, {
            "games_count": regular.get("scheduled"), "record": regular.get("winner_record"),
            "source_timestamp": source.get("verified_at"),
        }, at=at))
    ordered_results = [result for _, result in sorted(graded_sequence)]
    if ordered_results and ordered_results[-1] in {"WIN", "LOSS"}:
        latest = ordered_results[-1]
        streak = 0
        for result in reversed(ordered_results):
            if result != latest:
                break
            streak += 1
        if streak >= 3:
            events.append(_event("STREAK_MILESTONE", source_id, {
                "streak_count": streak, "streak_result": latest,
                "source_timestamp": source.get("verified_at"),
            }, at=at))
    return events


def event_identity(event: dict[str, Any], sha) -> tuple[str, str]:
    evidence_hash = sha(event["evidence"])
    event_id = sha({
        "event_type": event["event_type"], "game_id": event.get("game_id"),
        "source_id": event["source_id"], "evidence_hash": evidence_hash,
    })[:32]
    return event_id, evidence_hash


def store(connection, event: dict[str, Any], sha) -> dict[str, Any]:
    event_id, evidence_hash = event_identity(event, sha)
    connection.execute("""INSERT INTO social_events(
        event_id,event_type,game_id,source_id,prediction_id,model_version,model_hash,
        market_snapshot_id,grade_evidence_id,event_at,evidence_json,evidence_hash,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING""", (
        event_id, event["event_type"], event.get("game_id"), event["source_id"], event.get("prediction_id"),
        event.get("model_version"), event.get("model_hash"), event.get("market_snapshot_id"),
        event.get("grade_evidence_id"), event["event_at"], json_value(event["evidence"]),
        evidence_hash, event["event_at"],
    ))
    return {**event, "event_id": event_id, "evidence_hash": evidence_hash}
