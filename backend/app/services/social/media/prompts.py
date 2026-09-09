"""Creative-only prompts. Authoritative words and numbers are overlaid later."""
from __future__ import annotations


def creative_prompt(context: dict, template: str) -> str:
    matchup = " versus ".join(value for value in (context.get("away_team"), context.get("home_team")) if value)
    return (
        "Premium cinematic American football analytics artwork for a modern sports-media brand. "
        "Dark black and charcoal stadium atmosphere, emerald and cyan light accents, dynamic editorial composition, "
        "high contrast, sophisticated, realistic but not based on a real player photograph. "
        f"Theme: {template.replace('_', ' ').lower()}. Matchup mood: {matchup or 'weekly football slate'}. "
        "Leave clean negative space in the center and lower third for a deterministic data overlay. "
        "Do not render any words, letters, numbers, statistics, team logos, league marks, watermarks, or sponsor marks."
    )
