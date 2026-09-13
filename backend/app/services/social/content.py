"""Grounded captions with optional AI wording and deterministic fallback."""
from __future__ import annotations

import json
import os
import re
import hashlib
from typing import Any, Callable

import requests

from .models import CONTENT_TYPES
from backend.app.services.team_metadata import NFL_TEAMS, NBA_TEAMS


BANNED_PHRASES = (
    "guaranteed", "can't lose", "cannot lose", "free money", "100%", "easy win",
    "sure thing", "risk-free", "lock of the day", "bet the house", "guarantee",
)

INTERNAL_PUBLIC_PHRASES = (
    "verified pregame predictions remain", "candidate generated", "model lean frozen",
    "prediction records available", "eligible games remain", "board contains",
    "frozen predictions", "model artifact", "source envelope",
)
DEFAULT_HASHTAGS = "#NFL #NFLPicks #SportsBetting #SmartBets"


def team_display(value: Any, *, full: bool = False) -> str:
    """Turn a stored NFL identifier into natural public-facing copy."""
    raw = str(value or "").strip()
    folded = raw.casefold()
    for abbreviation, full_name, city, nickname in NFL_TEAMS:
        if folded in {abbreviation.casefold(), full_name.casefold(), nickname.casefold(),
                      f"{city} {nickname}".casefold()}:
            return full_name if full else nickname
    return raw


def _entity_forms(value: Any) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    forms = [raw]
    folded = raw.casefold()
    for abbreviation, full_name, city, nickname in NFL_TEAMS:
        if folded in {abbreviation.casefold(), full_name.casefold(), nickname.casefold(),
                      f"{city} {nickname}".casefold()}:
            forms.extend((abbreviation, full_name, city, nickname))
            break
    return forms


def _variant(context: dict[str, Any], count: int) -> int:
    seed = "|".join(str(context.get(key) or "") for key in
                    ("window_key", "game_id", "source_timestamp", "post_type"))
    return int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) % count


def _cta_url(context: dict[str, Any]) -> str:
    match = re.search(r"https://\S+", str(context.get("cta") or ""))
    return match.group(0).rstrip(".,") if match else ""


def _finish(body: str, context: dict[str, Any], label: str = "See the full board") -> str:
    suffix = ""
    url = _cta_url(context)
    if url:
        suffix += f"\n\n{label} → {url}"
    if os.getenv("SOCIAL_HASHTAGS_ENABLED", "true").lower() == "true":
        suffix += f"\n\n{DEFAULT_HASHTAGS}"
    text = body.strip() + suffix
    if len(text) > 280 and "#SportsBetting " in text:
        text = text.replace("#SportsBetting ", "")
    if len(text) > 280:
        raise ValueError("Social caption must contain 1-280 characters")
    return text


def _numbers(value: Any) -> set[str]:
    # Keep literal Unicode copy literal; JSON escaping an emoji can manufacture
    # digit-like code points (for example ``\u26a1``) that are not claims.
    encoded = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
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
    if any(phrase in lowered for phrase in BANNED_PHRASES) or re.search(r"\block\b", lowered):
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


def validate_content_quality(text: str, context: dict[str, Any]) -> str:
    """Fail closed when grounded copy still reads like an internal status log."""
    text = validate_caption(text, context)
    lowered = text.casefold()
    if any(phrase in lowered for phrase in INTERNAL_PUBLIC_PHRASES):
        raise ValueError("Internal system terminology is not suitable for public copy")
    kind = str(context.get("post_type") or "").upper()
    prediction_types = {"AI_PICK", "MODEL_VS_MARKET", "UPSET_WATCH", "LINE_MOVEMENT"}
    if kind in prediction_types:
        teams = (team_display(context.get("winner")), team_display(context.get("away_team")),
                 team_display(context.get("home_team")))
        if not any(team and team.casefold() in lowered for team in teams):
            raise ValueError("Prediction copy must mention a supported team")
        if context.get("model_probability") is not None and _pct(context["model_probability"]) not in text:
            raise ValueError("Prediction copy must include useful model information")
    if kind == "TODAYS_CARD":
        picks = context.get("ranked_picks") or []
        if picks and not any(team_display(pick.get("winner")).casefold() in lowered for pick in picks[:3]):
            raise ValueError("Slate copy must feature a real ranked model pick")
    if _cta_url(context) and _cta_url(context) not in text:
        raise ValueError("Configured CTA is missing")
    if os.getenv("SOCIAL_HASHTAGS_ENABLED", "true").lower() == "true" and "#smartbets" not in lowered:
        raise ValueError("Configured social hashtags are missing")
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
    away, home = team_display(context.get("away_team")), team_display(context.get("home_team"))
    matchup = f"{away} at {home}" if away and home else "today's slate"
    winner = team_display(context.get("winner"))
    probability = context.get("model_probability")
    market = context.get("market_probability")
    edge = context.get("edge")
    if kind == "AI_PICK":
        variants = (
            f"⚡ THE MODEL LIKES {winner.upper()}\n\n{matchup}\n🤖 Win probability: {_pct(probability)}\n\nAgree with the model?",
            f"🏈 SMARTBETS MODEL PICK\n\n{matchup}\nPick: {winner}\nWin probability: {_pct(probability)}",
            f"🤖 {winner.upper()} GETS THE NOD\n\n{matchup}\nSmartBets win probability: {_pct(probability)}",
        )
        text = _finish(variants[_variant(context, len(variants))], context, "See the matchup")
    elif kind == "TODAYS_CARD":
        picks = context.get("ranked_picks") or []
        lines = []
        for icon, pick in zip(("⚡", "🔥", "👀"), picks[:3]):
            if pick.get("winner") and pick.get("probability") is not None:
                lines.append(f"{icon} {team_display(pick['winner'])} {_pct(pick['probability'])}")
        headline = context.get("window_label") or "TODAY'S NFL BOARD"
        count = int(context.get("predictions_count") or len(picks))
        variants = (
            f"🏈 NFL WEEK {context.get('week')} — {headline}\n\n{count} SmartBets predictions still live.",
            f"🏈 {headline}\n\nThe SmartBets model has {count} Week {context.get('week')} picks left.",
            f"⚡ {headline}\n\n{count} games remain on the SmartBets board.",
        )
        body = variants[_variant(context, len(variants))]
        if lines:
            body += "\n\n" + "\n".join(lines)
        text = _finish(body, context)
    elif kind == "MODEL_VS_MARKET":
        if market is None or edge is None:
            raise ValueError("Market comparison requires verified market data")
        variants = (
            f"📊 MODEL VS MARKET\n\n{matchup}\n🤖 {winner}: {_pct(probability)}\nMarket: {_pct(market)} | Edge: {_pct(edge)}",
            f"👀 THE NUMBERS DISAGREE\n\nSmartBets has {winner} at {_pct(probability)}.\nMarket: {_pct(market)} | Model edge: {_pct(edge)}\n{matchup}",
        )
        text = _finish(variants[_variant(context, len(variants))], context, "See the analysis")
    elif kind == "UPSET_WATCH":
        if market is None:
            raise ValueError("Upset watch requires verified market data")
        variants = (
            f"🚨 UPSET WATCH\n\nThe market has {winner} as the underdog. SmartBets sees a live shot.\n\n🤖 {winner}: {_pct(probability)}\n{matchup}",
            f"👀 DOG WITH A CHANCE\n\nSmartBets has {winner} at {_pct(probability)}.\n{matchup}\n\nWho are you taking?",
        )
        text = _finish(variants[_variant(context, len(variants))], context, "See why")
    elif kind == "LINE_MOVEMENT":
        movement = context.get("movement")
        if movement is None:
            raise ValueError("Line movement requires two verified market observations")
        text = _finish(f"📈 LINE MOVE\n\n{matchup}\nMarket move: {_pct(movement)}\nSmartBets: {winner} at {_pct(probability)}", context, "Track the matchup")
    elif kind in {"CASHED", "WIN_RECEIPT"}:
        variants = (
            f"✅ SMARTBETS CALLED IT\n\nPick: {winner} ({_pct(probability)})\nFinal: {away} {context['away_score']}, {home} {context['home_score']}\n\nEvery result stays on the record.",
            f"✅ MODEL WIN\n\nSmartBets was on {winner} at {_pct(probability)}.\nFinal: {away} {context['away_score']}, {home} {context['home_score']}\n\nOne more result logged.",
        )
        text = _finish(variants[_variant(context, len(variants))], context, "Track every prediction")
    elif kind in {"MISS", "LOSS_RECEIPT"}:
        variants = (
            f"❌ MODEL MISS\n\nSmartBets picked {winner} ({_pct(probability)}).\nFinal: {away} {context['away_score']}, {home} {context['home_score']}\n\nLoss recorded. No hiding it.",
            f"❌ NOT THIS TIME\n\nThe model was on {winner} at {_pct(probability)}.\nFinal: {away} {context['away_score']}, {home} {context['home_score']}\n\nThe miss stays in the history.",
        )
        text = _finish(variants[_variant(context, len(variants))], context, "See the full record")
    elif kind == "DAILY_RECAP":
        text = _finish(f"📊 SUNDAY RECAP\n\n{context.get('finals_count', 0)} finals in. SmartBets is {_record(context.get('record'))} on winner picks today.\n\nWins and misses—all tracked.", context, "See the results")
    elif kind in {"WEEKLY_REPORT", "MODEL_RECAP"}:
        text = _finish(f"📈 WEEKLY REPORT\n\nSmartBets: {_record(context.get('record'))} across {context.get('games_count', 0)} games.\n\nEvery pick. Every result. No selective history.", context, "View the week")
    elif kind == "STREAK_MILESTONE":
        text = _finish(f"🔥 MODEL STREAK\n\n{context['streak_count']} straight {context['streak_result'].lower()} results for SmartBets.\n\nThe full record stays visible.", context, "View the results")
    elif kind == "ENGAGEMENT_QUESTION":
        text = _finish(f"🏈 {matchup}\n\nSmartBets is on {winner}. Who are you taking?", context, "See the model")
    else:
        kickoff = context.get("kickoff_label") or "Kickoff ahead"
        lean = f"\nSmartBets: {winner} ({_pct(probability)})" if winner and probability is not None else ""
        variants = (
            f"🏈 GAME PREVIEW\n\n{matchup}\n{kickoff}{lean}\n\nWho gets it done?",
            f"⏰ KICKOFF AHEAD\n\n{matchup}{lean}\n\nAgree with SmartBets?",
        )
        text = _finish(variants[_variant(context, len(variants))], context, "See the matchup")
    return validate_content_quality(text, context)


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
        "season": evidence.get("season"), "week": evidence.get("week"),
        "window_key": evidence.get("window_key"), "window_label": evidence.get("window_label"),
        "next_kickoff": evidence.get("next_kickoff"), "ranked_picks": evidence.get("ranked_picks") or [],
        "prediction_id": prediction.get("prediction_hash") or prediction.get("artifact_hash"),
        "stored_wager_evidence": bool((evidence.get("grade") or {}).get("qualified_wager")),
        "source_timestamp": evidence.get("source_timestamp"), "cta": cta,
        "source_entities": [],
    }
    entity_values = [evidence.get("away_team"), evidence.get("home_team"), prediction.get("winner")]
    for pick in context["ranked_picks"]:
        entity_values.extend((pick.get("away_team"), pick.get("home_team"), pick.get("winner")))
    context["source_entities"] = list(dict.fromkeys(
        form for value in entity_values for form in _entity_forms(value)))
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
        return validate_content_quality(text, context)


def compose(context: dict[str, Any], writer_factory: Callable[[], Callable] = OpenAICopyWriter) -> tuple[str, str]:
    fallback = deterministic_caption(context)
    if os.getenv("SOCIAL_AI_COPY_ENABLED", "false").lower() != "true":
        return fallback, "deterministic"
    try:
        return validate_content_quality(writer_factory()(context), context), "openai"
    except Exception:
        return fallback, "deterministic_fallback"
