"""Grounded captions with optional AI wording and deterministic fallback."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable

import requests

from .models import CONTENT_TYPES
from backend.app.services.team_metadata import NFL_TEAMS, NBA_TEAMS


BANNED_PHRASES = (
    "guaranteed", "can't lose", "cannot lose", "free money", "100%", "easy win",
    "sure thing", "risk-free", "lock of the day", "bet the house",
)


def _numbers(value: Any) -> set[str]:
    encoded = json.dumps(value, sort_keys=True)
    result = set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", encoded))
    for raw in list(result):
        try:
            number = float(raw)
        except ValueError:
            continue
        if 0 <= number <= 1:
            result.add(f"{number * 100:.1f}")
        if number.is_integer():
            result.add(str(int(number)))
    return result


def validate_caption(text: str, context: dict[str, Any]) -> str:
    text = str(text or "").strip()
    if not text or len(text) > 280:
        raise ValueError("Social caption must contain 1-280 characters")
    lowered = text.casefold()
    if any(phrase in lowered for phrase in BANNED_PHRASES):
        raise ValueError("Unsafe social phrasing blocked")
    if re.search(r"(?<!\w)@[A-Za-z0-9_]+", text):
        raise ValueError("Unsolicited mentions are not supported")
    supplied = _numbers(context)
    introduced = _numbers(text) - supplied
    if introduced:
        raise ValueError("Social caption introduced an unsupported number")
    allowed_entities = " ".join(str(value) for value in context.get("source_entities", [])).casefold()
    for _abbr, name, city, nickname in (*NFL_TEAMS, *NBA_TEAMS):
        forms = {name.casefold(), nickname.casefold()}
        mentioned = any(re.search(rf"(?<!\w){re.escape(form)}(?!\w)", lowered) for form in forms)
        allowed = any(form in allowed_entities or allowed_entities in form for form in forms if allowed_entities)
        if mentioned and not allowed:
            raise ValueError("Social caption introduced an unsupported team entity")
    if any(word in lowered for word in ("cashed", "wager", "bet won", "bet lost")) and not context.get("stored_wager_evidence"):
        # Receipt labels refer to model prediction outcomes unless actual wager
        # evidence exists.  Templates use "prediction win/miss" accordingly.
        raise ValueError("A prediction cannot be represented as a wager")
    return text


def _pct(value: Any) -> str:
    number = float(value)
    if number <= 1:
        number *= 100
    return f"{number:.1f}%"


def _record(value: Any) -> str:
    value = value or {}
    return f"{int(value.get('WIN', 0))}-{int(value.get('LOSS', 0))}-{int(value.get('PUSH', 0))}"


def deterministic_caption(context: dict[str, Any]) -> str:
    kind = str(context.get("post_type") or "").upper()
    if kind not in CONTENT_TYPES:
        raise ValueError("Unsupported social post type")
    away, home = context.get("away_team"), context.get("home_team")
    matchup = f"{away} at {home}" if away and home else "today's slate"
    winner = context.get("winner")
    probability = context.get("model_probability")
    market = context.get("market_probability")
    edge = context.get("edge")
    if kind == "AI_PICK":
        text = f"SMARTBETS AI PICK\n{matchup}\nModel lean: {winner} ({_pct(probability)})\nA projection, not a promise."
    elif kind == "TODAYS_CARD":
        text = f"TODAY'S CARD\n{context['predictions_count']} verified predictions across {context['games_count']} games. Every lean is frozen before kickoff."
    elif kind == "MODEL_VS_MARKET":
        if market is None or edge is None:
            raise ValueError("Market comparison requires verified market data")
        text = f"SMARTBETS vs MARKET\n{matchup}\nModel: {_pct(probability)} | Market: {_pct(market)} | Edge: {_pct(edge)}\nNumbers captured before kickoff."
    elif kind == "UPSET_WATCH":
        if market is None:
            raise ValueError("Upset watch requires verified market data")
        text = f"UPSET WATCH\n{matchup}\nThe model leans {winner} at {_pct(probability)} while the market prices the other side shorter. Worth watching—not a guarantee."
    elif kind == "LINE_MOVEMENT":
        movement = context.get("movement")
        if movement is None:
            raise ValueError("Line movement requires two verified market observations")
        text = f"LINE MOVEMENT\n{matchup}\nVerified implied-probability move: {_pct(movement)}. The model lean remains {winner} at {_pct(probability)}."
    elif kind in {"CASHED", "WIN_RECEIPT"}:
        text = f"PREDICTION WIN\n{matchup}\nFrozen pick: {winner} ({_pct(probability)})\nFinal: {context['away_score']}-{context['home_score']}\nReceipts include the wins and the misses."
    elif kind in {"MISS", "LOSS_RECEIPT"}:
        text = f"PREDICTION MISS\n{matchup}\nFrozen pick: {winner} ({_pct(probability)})\nFinal: {context['away_score']}-{context['home_score']}\nNo hiding the result."
    elif kind == "DAILY_RECAP":
        text = f"DAILY RECAP\n{context.get('finals_count', 0)} finals are verified. Current prediction record: {_record(context.get('record'))}. Predictions and wagers are tracked separately."
    elif kind in {"WEEKLY_REPORT", "MODEL_RECAP"}:
        text = f"WEEKLY REPORT\nVerified prediction record: {_record(context.get('record'))} across {context.get('games_count', 0)} games. One sample, not a promise of future results."
    elif kind == "STREAK_MILESTONE":
        text = f"MODEL MILESTONE\n{context['streak_count']} straight verified {context['streak_result'].lower()} results. The full record stays visible."
    elif kind == "ENGAGEMENT_QUESTION":
        text = f"{matchup}\nThe SmartBets model leans {winner}. What matchup factor matters most to your read?"
    else:
        kickoff = context.get("kickoff_label") or "Kickoff ahead"
        lean = f"\nModel lean: {winner} ({_pct(probability)})" if winner and probability is not None else ""
        text = f"GAME PREVIEW\n{matchup}\n{kickoff}{lean}\nWhat are you watching?"
    cta = context.get("cta") or ""
    return validate_caption(text + cta, context)


def context_from_event(event: dict[str, Any], post_type: str, cta: str = "") -> dict[str, Any]:
    evidence = event.get("evidence") or {}
    prediction = evidence.get("prediction") or {}
    market = evidence.get("market") or {}
    final = evidence.get("final") or {}
    context = {
        "post_type": post_type, "game_id": evidence.get("game_id"),
        "away_team": evidence.get("away_team"), "home_team": evidence.get("home_team"),
        "winner": prediction.get("winner"), "model_probability": prediction.get("probability"),
        "market_probability": market.get("probability"), "edge": market.get("edge"),
        "movement": market.get("movement"), "snapshot_id": market.get("snapshot_id"),
        "away_score": final.get("away_score"), "home_score": final.get("home_score"),
        "games_count": evidence.get("games_count"), "predictions_count": evidence.get("predictions_count"),
        "finals_count": evidence.get("finals_count"), "record": evidence.get("record"),
        "streak_count": evidence.get("streak_count"), "streak_result": evidence.get("streak_result"),
        "kickoff_time": evidence.get("kickoff_time"), "kickoff_label": evidence.get("kickoff_label"),
        "stored_wager_evidence": bool((evidence.get("grade") or {}).get("qualified_wager")),
        "source_timestamp": evidence.get("source_timestamp"), "cta": cta,
        "source_entities": [value for value in (evidence.get("away_team"), evidence.get("home_team"), prediction.get("winner")) if value],
    }
    return context


class OpenAICopyWriter:
    """Optional Responses API adapter.  It receives only the whitelisted context."""

    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("SOCIAL_OPENAI_TEXT_MODEL", "")
        if not self.api_key or not self.model:
            raise ValueError("OpenAI copy generation is not configured")

    def __call__(self, context: dict[str, Any]) -> str:
        safe = {key: value for key, value in context.items() if key not in {"stored_wager_evidence"}}
        response = self.session.post(self.endpoint, headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
        }, json={
            "model": self.model,
            "instructions": "Write one concise sports-media X caption using only supplied facts. Never add numbers, teams, injuries, news, mentions, guarantees, or betting claims. Return caption text only.",
            "input": json.dumps(safe, sort_keys=True), "max_output_tokens": 180,
        }, timeout=25, allow_redirects=False)
        if response.status_code != 200:
            raise RuntimeError(f"OPENAI_COPY_HTTP_{response.status_code}")
        payload = response.json()
        text = payload.get("output_text")
        if not text:
            for item in payload.get("output", []):
                for part in item.get("content", []):
                    if part.get("type") == "output_text":
                        text = part.get("text")
                        break
        return validate_caption(text, context)


def compose(context: dict[str, Any], writer_factory: Callable[[], Callable] = OpenAICopyWriter) -> tuple[str, str]:
    fallback = deterministic_caption(context)
    if os.getenv("SOCIAL_AI_COPY_ENABLED", "false").lower() != "true":
        return fallback, "deterministic"
    try:
        return validate_caption(writer_factory()(context), context), "openai"
    except Exception:
        return fallback, "deterministic_fallback"
