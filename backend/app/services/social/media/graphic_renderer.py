"""Deterministic branded overlay renderer; AI pixels never supply facts."""
from __future__ import annotations

from io import BytesIO
from typing import Any


WIDTH, HEIGHT = 1600, 900


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
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


def render_graphic(context: dict[str, Any], template: str, background: bytes | None = None) -> bytes:
    from PIL import Image, ImageDraw
    if background:
        image = _cover(Image.open(BytesIO(background)))
    else:
        image = Image.new("RGB", (WIDTH, HEIGHT), "#071019")
        draw = ImageDraw.Draw(image)
        for x in range(WIDTH):
            blend = x / WIDTH
            draw.line((x, 0, x, HEIGHT), fill=(5, int(18 + 25 * blend), int(28 + 35 * blend)))
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 0, WIDTH, HEIGHT), fill=(1, 7, 12, 95))
    draw.rounded_rectangle((70, 54, WIDTH - 70, HEIGHT - 54), radius=32, fill=(3, 12, 20, 200), outline=(45, 212, 191, 180), width=3)
    draw.text((112, 90), "SMARTBETSPORTS", font=_font(38, True), fill="#67E8F9")
    draw.text((112, 160), template.replace("_", " "), font=_font(72, True), fill="white")
    away, home = context.get("away_team"), context.get("home_team")
    if away and home:
        draw.text((112, 285), f"{away}  at  {home}", font=_font(54, True), fill="#E2E8F0")
    winner, probability = context.get("winner"), context.get("model_probability")
    if winner and probability is not None:
        value = float(probability) * 100 if float(probability) <= 1 else float(probability)
        draw.text((112, 405), "MODEL LEAN", font=_font(28, True), fill="#94A3B8")
        draw.text((112, 454), str(winner), font=_font(62, True), fill="#34D399")
        draw.text((112, 535), f"{value:.1f}%", font=_font(88, True), fill="white")
    if context.get("market_probability") is not None and context.get("edge") is not None:
        market = float(context["market_probability"]); edge = float(context["edge"])
        if market <= 1: market *= 100
        if abs(edge) <= 1: edge *= 100
        draw.text((900, 420), "MARKET", font=_font(28, True), fill="#94A3B8")
        draw.text((900, 462), f"{market:.1f}%", font=_font(66, True), fill="white")
        draw.text((1190, 420), "EDGE", font=_font(28, True), fill="#94A3B8")
        draw.text((1190, 462), f"{edge:+.1f}%", font=_font(66, True), fill="#22D3EE")
    if context.get("away_score") is not None and context.get("home_score") is not None:
        draw.text((112, 650), f"FINAL  {context['away_score']} - {context['home_score']}", font=_font(58, True), fill="#F8FAFC")
    draw.text((112, 790), "Verified data. Frozen picks. Transparent results.", font=_font(28), fill="#CBD5E1")
    result = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    output = BytesIO(); result.save(output, format="PNG", optimize=True)
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
