"""Shared utility helpers for the internal backtesting package."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    """Return the current UTC timestamp in a stable ISO-8601 format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def timestamp_slug() -> str:
    """Return a filesystem-safe UTC timestamp for result directories."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def stable_hash(payload: Any) -> str:
    """Return a deterministic SHA-256 hash for JSON-serializable configuration."""
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_json(path: Path, default: Any) -> Any:
    """Read JSON from ``path`` or return ``default`` when the snapshot is absent."""
    if not path.exists():
        return default
    return json.loads(path.read_text())


def git_commit_hash() -> str | None:
    """Return the current git commit hash when the repository metadata is available."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return result.stdout.strip() or None
