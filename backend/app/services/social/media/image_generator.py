"""OpenAI creative-background provider abstraction."""
from __future__ import annotations

import base64
import os

import requests


class OpenAIImageGenerator:
    endpoint = "https://api.openai.com/v1/images/generations"

    def __init__(self, session=None):
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("SOCIAL_OPENAI_IMAGE_MODEL", "gpt-image-2.5-sunburst")
        self.session = session or requests.Session()
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required for AI images")

    def generate(self, prompt: str, *, quality: str) -> tuple[bytes, str]:
        response = self.session.post(self.endpoint, headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
        }, json={"model": self.model, "prompt": prompt, "size": "1536x1024", "quality": quality,
                 "output_format": "png", "n": 1}, timeout=55, allow_redirects=False)
        if response.status_code != 200:
            raise RuntimeError(f"OPENAI_IMAGE_HTTP_{response.status_code}")
        encoded = (response.json().get("data") or [{}])[0].get("b64_json")
        if not encoded:
            raise RuntimeError("OPENAI_IMAGE_MALFORMED_RESPONSE")
        return base64.b64decode(encoded, validate=True), self.model
