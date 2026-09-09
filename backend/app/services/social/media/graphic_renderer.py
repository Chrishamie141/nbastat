"""Deterministic, feed-readable branded social graphics.

AI artwork is intentionally decorative. Every authoritative name, score and
percentage is rendered here from verified source context.
"""
from __future__ import annotations

from io import BytesIO
from typing import Any

from backend.app.services.team_metadata import NBA_TEAMS, NFL_TEAMS


WIDTH, HEIGHT = 1600, 900
CYAN = "#48E7E2"
EMERALD = "#35E49A"
WHITE = "#F8FAFC"
MUTED = "#A9B8C7"
INK = "#061018"
WIN_TYPES = frozenset({"CASHED", "WIN_RECEIPT"})
LOSS_TYPES = frozenset({"MISS", "LOSS_RECEIPT"})
RECAP_TYPES = frozenset({"DAILY_RECAP", "WEEKLY_REPORT", "MODEL_RECAP"})


def _font(size: int, bold: bool = False, condensed: bool = False):
    from PIL import ImageFont

    linux_name = "DejaVuSansCondensed-Bold.ttf" if bold or condensed else "DejaVuSans.ttf"
    windows_name = "arialbd.ttf" if bold or condensed else "arial.ttf"
    candidates = [
        f"/usr/share/fonts/truetype/dejavu/{linux_name}",
        f"C:/Windows/Fonts/{windows_name}",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _cover(image):
    from PIL import ImageOps

    return ImageOps.fit(image.convert("RGB"), (WIDTH, HEIGHT), method=3)


def _pct(value: Any) -> float:
    number = float(value)
    return number * 100 if abs(number) <= 1 else number


def _team_code(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        return ""
    folded = name.casefold()
    for abbreviation, full_name, city, nickname in (*NFL_TEAMS, *NBA_TEAMS):
        if folded in {abbreviation.casefold(), full_name.casefold(), nickname.casefold()}:
            return abbreviation
        if folded == f"{city} {nickname}".casefold():
            return abbreviation
    words = [word for word in name.replace("-", " ").split() if word]
    return "".join(word[0] for word in words)[:4].upper() or name[:4].upper()


def _fit_font(draw, text: str, max_width: int, start: int, minimum: int = 36):
    size = start
    while size > minimum:
        font = _font(size, True, condensed=True)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
        size -= 4
    return _font(minimum, True, condensed=True)


def _vertical_gradient(size: tuple[int, int], top: tuple[int, int, int, int], bottom: tuple[int, int, int, int]):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", size, top)
    draw = ImageDraw.Draw(image)
    height = max(1, size[1] - 1)
    for y in range(size[1]):
        ratio = y / height
        color = tuple(round(top[index] + (bottom[index] - top[index]) * ratio) for index in range(4))
        draw.line((0, y, size[0], y), fill=color)
    return image


def _horizontal_gradient(size: tuple[int, int], left: tuple[int, int, int, int], right: tuple[int, int, int, int]):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", size, left)
    draw = ImageDraw.Draw(image)
    width = max(1, size[0] - 1)
    for x in range(size[0]):
        ratio = x / width
        color = tuple(round(left[index] + (right[index] - left[index]) * ratio) for index in range(4))
        draw.line((x, 0, x, size[1]), fill=color)
    return image


def _draw_brand(draw) -> None:
    draw.rounded_rectangle((72, 58, 104, 90), radius=9, fill=CYAN)
    draw.rounded_rectangle((84, 70, 116, 102), radius=9, outline=EMERALD, width=4)
    draw.text((136, 55), "SMARTBET", font=_font(34, True, condensed=True), fill=WHITE)
    draw.text((352, 55), "SPORTS", font=_font(34, True, condensed=True), fill=CYAN)
    draw.text((137, 94), "VERIFIED MODEL INTELLIGENCE", font=_font(16, True), fill=MUTED)


def _draw_type_badge(draw, template: str) -> None:
    label = {
        "AI_PICK": "AI PICK",
        "GAME_PREVIEW": "GAME PREVIEW",
        "MODEL_VS_MARKET": "MODEL VS MARKET",
        "UPSET_WATCH": "UPSET WATCH",
        "LINE_MOVEMENT": "LINE MOVEMENT",
        "CASHED": "PREDICTION WIN",
        "WIN_RECEIPT": "PREDICTION WIN",
        "MISS": "PREDICTION MISS",
        "LOSS_RECEIPT": "PREDICTION MISS",
        "TODAYS_CARD": "TODAY'S CARD",
        "DAILY_RECAP": "DAILY RECAP",
        "WEEKLY_REPORT": "WEEKLY REPORT",
        "MODEL_RECAP": "MODEL REPORT",
        "STREAK_MILESTONE": "MODEL MILESTONE",
    }.get(template, template.replace("_", " "))
    font = _font(22, True, condensed=True)
    width = draw.textbbox((0, 0), label, font=font)[2] + 54
    left = WIDTH - 72 - width
    draw.rounded_rectangle((left, 62, WIDTH - 72, 111), radius=24, fill=(4, 19, 27, 215), outline=(72, 231, 226, 175), width=2)
    draw.text((left + 27, 73), label, font=font, fill=CYAN)


def _draw_matchup(draw, context: dict[str, Any]) -> int:
    away, home = str(context.get("away_team") or ""), str(context.get("home_team") or "")
    if not away or not home:
        return 165
    away_code, home_code = _team_code(away), _team_code(home)
    matchup = f"{away_code}  /  {home_code}"
    font = _fit_font(draw, matchup, 940, 98, 62)
    draw.text((72, 155), matchup, font=font, fill=WHITE, stroke_width=2, stroke_fill=(5, 16, 24))
    full_matchup = f"{away.upper()}  AT  {home.upper()}"
    draw.text((77, 264), full_matchup, font=_fit_font(draw, full_matchup, 980, 27, 20), fill=MUTED)
    draw.rectangle((72, 310, 308, 318), fill=CYAN)
    draw.rectangle((308, 310, 462, 318), fill=EMERALD)
    return 348


def _draw_stat_tile(draw, x: int, y: int, label: str, value: str, color: str = WHITE, width: int = 250) -> None:
    draw.rounded_rectangle((x, y, x + width, y + 128), radius=22, fill=(5, 17, 25, 220), outline=(255, 255, 255, 35), width=2)
    draw.text((x + 24, y + 18), label, font=_font(18, True), fill=MUTED)
    draw.text((x + 24, y + 49), value, font=_fit_font(draw, value, width - 48, 50, 34), fill=color)


def _draw_pick(draw, context: dict[str, Any], y: int) -> None:
    winner = str(context.get("winner") or "")
    probability = context.get("model_probability")
    if not winner or probability is None:
        return
    value = _pct(probability)
    draw.text((72, y), "THE MODEL LEANS", font=_font(24, True), fill=CYAN)
    winner_font = _fit_font(draw, winner.upper(), 900, 92, 58)
    draw.text((68, y + 36), winner.upper(), font=winner_font, fill=WHITE, stroke_width=2, stroke_fill=INK)
    draw.text((72, y + 142), f"{value:.1f}", font=_font(142, True, condensed=True), fill=EMERALD, stroke_width=2, stroke_fill=INK)
    draw.text((430, y + 180), "%", font=_font(66, True), fill=EMERALD)
    draw.text((72, y + 294), "MODEL PROBABILITY", font=_font(20, True), fill=MUTED)

    market = context.get("market_probability")
    edge = context.get("edge")
    if market is not None and edge is not None:
        _draw_stat_tile(draw, 560, y + 157, "MARKET", f"{_pct(market):.1f}%")
        _draw_stat_tile(draw, 830, y + 157, "MODEL EDGE", f"{_pct(edge):+.1f}%", CYAN)


def _draw_receipt(draw, context: dict[str, Any], template: str, y: int) -> None:
    is_win = template in WIN_TYPES
    result = "WIN" if is_win else "MISS"
    color = EMERALD if is_win else "#FB7185"
    draw.text((72, y), "FROZEN PREDICTION RESULT", font=_font(24, True), fill=MUTED)
    draw.text((66, y + 30), result, font=_font(154, True, condensed=True), fill=color, stroke_width=3, stroke_fill=INK)
    if context.get("away_score") is not None and context.get("home_score") is not None:
        score = f"{context['away_score']}  -  {context['home_score']}"
        _draw_stat_tile(draw, 560, y + 74, "FINAL SCORE", score, WHITE, 390)
    winner, probability = context.get("winner"), context.get("model_probability")
    if winner and probability is not None:
        detail = f"PICK  {str(winner).upper()}  •  {_pct(probability):.1f}%"
        draw.text((72, y + 220), detail, font=_fit_font(draw, detail, 920, 34, 24), fill=WHITE)


def _draw_recap(draw, context: dict[str, Any], template: str, y: int) -> None:
    record = context.get("record") or {}
    wins = int(record.get("WIN", 0)); losses = int(record.get("LOSS", 0)); pushes = int(record.get("PUSH", 0))
    headline = "WEEK IN REVIEW" if template in {"WEEKLY_REPORT", "MODEL_RECAP"} else "TODAY'S RESULTS"
    draw.text((72, y), headline, font=_font(27, True), fill=CYAN)
    draw.text((67, y + 32), f"{wins}-{losses}-{pushes}", font=_font(144, True, condensed=True), fill=WHITE, stroke_width=3, stroke_fill=INK)
    draw.text((73, y + 188), "WINS  /  LOSSES  /  PUSHES", font=_font(22, True), fill=MUTED)
    count = context.get("games_count") or context.get("finals_count")
    if count is not None:
        _draw_stat_tile(draw, 620, y + 67, "VERIFIED GAMES", str(count), CYAN, 300)


def render_graphic(context: dict[str, Any], template: str, background: bytes | None = None) -> bytes:
    from PIL import Image, ImageDraw, ImageEnhance

    template = str(template or context.get("post_type") or "GAME_PREVIEW").upper()
    if background:
        image = _cover(Image.open(BytesIO(background)))
        image = ImageEnhance.Color(image).enhance(0.82)
        image = ImageEnhance.Contrast(image).enhance(1.08)
    else:
        image = Image.new("RGB", (WIDTH, HEIGHT), "#071019")
        base = ImageDraw.Draw(image)
        for x in range(WIDTH):
            blend = x / WIDTH
            base.line((x, 0, x, HEIGHT), fill=(5, int(17 + 20 * blend), int(27 + 34 * blend)))
        # A subtle stadium-light motif keeps deterministic fallback graphics complete.
        for radius, alpha in ((290, 22), (205, 35), (120, 55)):
            base.ellipse((1230 - radius, 210 - radius, 1230 + radius, 210 + radius), fill=(9, 55 + alpha, 74 + alpha))

    image = image.convert("RGBA")
    # Localized scrims protect text without burying the creative artwork.
    image = Image.alpha_composite(image, _horizontal_gradient((WIDTH, HEIGHT), (1, 8, 13, 242), (1, 8, 13, 10)))
    image = Image.alpha_composite(image, _vertical_gradient((WIDTH, HEIGHT), (0, 0, 0, 18), (0, 4, 8, 184)))
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 0, WIDTH, 12), fill=CYAN)
    draw.polygon(((WIDTH - 430, 0), (WIDTH, 0), (WIDTH, 250), (WIDTH - 170, 105)), fill=(53, 228, 154, 38))
    draw.polygon(((WIDTH - 280, HEIGHT), (WIDTH, HEIGHT - 210), (WIDTH, HEIGHT),), fill=(72, 231, 226, 40))
    _draw_brand(draw)
    _draw_type_badge(draw, template)
    content_y = _draw_matchup(draw, context)

    if template in WIN_TYPES | LOSS_TYPES:
        _draw_receipt(draw, context, template, content_y)
    elif template in RECAP_TYPES:
        _draw_recap(draw, context, template, content_y)
    elif template == "TODAYS_CARD":
        draw.text((72, content_y), "THE VERIFIED SLATE", font=_font(28, True), fill=CYAN)
        count = context.get("predictions_count") or 0
        draw.text((66, content_y + 28), str(count), font=_font(164, True, condensed=True), fill=WHITE)
        draw.text((72, content_y + 198), "FROZEN PREDICTIONS", font=_font(25, True), fill=MUTED)
        if context.get("games_count") is not None:
            _draw_stat_tile(draw, 500, content_y + 78, "GAMES", str(context["games_count"]), CYAN)
    elif template == "STREAK_MILESTONE":
        count = int(context.get("streak_count") or 0)
        result = str(context.get("streak_result") or "RESULT").upper()
        draw.text((72, content_y), "CURRENT VERIFIED STREAK", font=_font(26, True), fill=CYAN)
        draw.text((66, content_y + 28), str(count), font=_font(176, True, condensed=True), fill=WHITE)
        draw.text((285, content_y + 126), result + "S", font=_font(66, True), fill=EMERALD if result == "WIN" else "#FB7185")
    else:
        _draw_pick(draw, context, content_y)

    draw.text((72, 838), "FROZEN BEFORE KICKOFF", font=_font(19, True), fill=WHITE)
    draw.ellipse((353, 846, 361, 854), fill=CYAN)
    draw.text((380, 838), "PREDICTIONS ARE NOT WAGERS", font=_font(19, True), fill=MUTED)
    draw.text((WIDTH - 370, 835), "SMARTBETSPORTS.COM", font=_font(21, True), fill=CYAN)

    result = Image.alpha_composite(image, overlay).convert("RGB")
    output = BytesIO()
    result.save(output, format="PNG", optimize=True)
    payload = output.getvalue()
    validate_image(payload, context)
    return payload


def validate_image(payload: bytes, context: dict[str, Any]) -> None:
    from PIL import Image

    if not payload:
        raise ValueError("Generated image is empty")
    image = Image.open(BytesIO(payload))
    if image.format != "PNG" or image.size != (WIDTH, HEIGHT):
        raise ValueError("Generated image has invalid format or dimensions")
    if context.get("winner") and context.get("model_probability") is None:
        raise ValueError("Required overlay values are incomplete")
