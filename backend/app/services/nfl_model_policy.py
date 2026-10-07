"""Authoritative NFL model-governance policy.

This module deliberately separates production serving from research results.
Changing a label here does not promote a model: promotion requires a reviewed
policy change and the production audit verifies that frozen winner predictions
continue to use the champion model.
"""
from __future__ import annotations

from backtesting.nfl_game_predictor import V2_MODEL_VERSION, V3_MODEL_VERSION

# Keep production policy independent of the research runner (and its optional
# numerical dependencies) while retaining the research artifact's stable ID.
PROP_V4_MODEL_VERSION = "nfl_prop_v4_research_v1"


NFL_MODEL_POLICY = {
    "winner": {
        "productionChampion": V2_MODEL_VERSION,
        "challenger": V3_MODEL_VERSION,
        "challengerStatus": "RESEARCH_ONLY",
    },
    "playerProps": {
        "challenger": PROP_V4_MODEL_VERSION,
        "status": "RESEARCH_ONLY",
        "promotionBlocked": True,
        "promotionRequirement": "Positive forward-test ROI with adequate calibration and sample size.",
    },
    "parlays": {
        "status": "EXPERIMENTAL",
        "jointProbabilityStatus": "UNAVAILABLE_CORRELATED_MODEL_REQUIRED",
        "independenceProductMayBePublished": False,
        "policy": "Individual leg probabilities may be shown; a joint probability requires a validated correlation-aware model.",
    },
}


def model_policy() -> dict:
    """Return a copy safe for API/audit responses."""
    return {
        section: dict(values)
        for section, values in NFL_MODEL_POLICY.items()
    }


def winner_model_is_authorized(model_version: str | None, *, season_type: str) -> bool:
    """Only regular-season winner predictions are governed by the V2 champion."""
    if str(season_type).lower() != "regular":
        return True
    return str(model_version or "") == V2_MODEL_VERSION
