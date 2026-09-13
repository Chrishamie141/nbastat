"""Creative-only prompts. Authoritative words and numbers are overlaid later."""
from __future__ import annotations


def creative_prompt(context: dict, template: str) -> str:
    matchup = " versus ".join(value for value in (context.get("away_team"), context.get("home_team")) if value)
    return (
        "Premium cinematic American football key art for a modern broadcast sports-media brand. "
        "Full-bleed night stadium atmosphere, realistic anonymous football athletes in motion, dramatic rim light, "
        "black and charcoal shadows with emerald and cyan accents, energetic diagonal composition, atmospheric depth, "
        "high contrast, sophisticated editorial finish, realistic but not based on a real player photograph. "
        f"Theme: {template.replace('_', ' ').lower()}. Matchup mood: {matchup or 'weekly football slate'}. "
        "The artwork is secondary to an information-first deterministic data overlay. Keep the left two-thirds readable "
        "with restrained texture; place the strongest athlete/stadium detail toward the right third and outer edges. "
        "Avoid blank empty space and avoid UI-card shapes, central subjects, or visual clutter behind the data area. "
        "Do not render any words, letters, numbers, statistics, team logos, league marks, watermarks, or sponsor marks."
    )
