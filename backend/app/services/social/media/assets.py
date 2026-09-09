"""Durable media object storage adapters."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

import requests


class LocalMediaStorage:
    def __init__(self, root: Path | None = None):
        if os.getenv("VERCEL"):
            raise ValueError("Local social media storage is not durable on Vercel")
        self.root = (root or Path(os.getenv("SOCIAL_MEDIA_LOCAL_DIR", ".runtime/social/media"))).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, payload: bytes, content_type: str) -> str:
        target = (self.root / key).resolve()
        if self.root not in target.parents:
            raise ValueError("Unsafe media storage key")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError("Media versions are immutable")
        target.write_bytes(payload)
        return str(target)

    def get(self, key: str) -> bytes:
        target = (self.root / key).resolve()
        if self.root not in target.parents:
            raise ValueError("Unsafe media storage key")
        return target.read_bytes()


class SupabaseMediaStorage:
    def __init__(self, session=None):
        self.url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self.key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        self.bucket = os.getenv("SOCIAL_MEDIA_STORAGE_BUCKET", "smartbets-social")
        if not self.url or not self.key:
            raise ValueError("Supabase media storage is not configured")
        self.session = session or requests.Session()

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.key}", "apikey": self.key}

    def put(self, key: str, payload: bytes, content_type: str) -> str:
        path = quote(f"{self.bucket}/{key}", safe="/")
        response = self.session.post(f"{self.url}/storage/v1/object/{path}", data=payload,
                                     headers={**self.headers, "Content-Type": content_type, "x-upsert": "false"},
                                     timeout=30, allow_redirects=False)
        if response.status_code not in {200, 201}:
            raise RuntimeError(f"SOCIAL_STORAGE_HTTP_{response.status_code}")
        return f"supabase://{self.bucket}/{key}"

    def get(self, key: str) -> bytes:
        path = quote(f"{self.bucket}/{key}", safe="/")
        response = self.session.get(f"{self.url}/storage/v1/object/{path}", headers=self.headers,
                                    timeout=30, allow_redirects=False)
        if response.status_code != 200:
            raise RuntimeError(f"SOCIAL_STORAGE_HTTP_{response.status_code}")
        return response.content


def storage_factory():
    provider = os.getenv("SOCIAL_MEDIA_STORAGE_PROVIDER", "local").lower()
    if provider == "local":
        return LocalMediaStorage()
    if provider == "supabase":
        return SupabaseMediaStorage()
    raise ValueError("Unsupported social media storage provider")
