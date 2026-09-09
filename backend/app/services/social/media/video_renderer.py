"""Server-compatible short-form video rendering using FFmpeg."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import os
from pathlib import Path

VIDEO_WIDTH, VIDEO_HEIGHT = 1280, 720


def available() -> bool:
    return shutil.which(os.getenv("SOCIAL_FFMPEG_PATH", "ffmpeg")) is not None


def render_video(image: bytes, duration_seconds: int = 15) -> bytes:
    binary = os.getenv("SOCIAL_FFMPEG_PATH", "ffmpeg")
    if not shutil.which(binary):
        raise RuntimeError("FFMPEG_UNAVAILABLE")
    duration_seconds = max(10, min(int(duration_seconds), 20))
    with tempfile.TemporaryDirectory(prefix="smartbets-social-") as directory:
        root = Path(directory); source = root / "frame.png"; target = root / "social.mp4"
        source.write_bytes(image)
        command = [binary, "-hide_banner", "-loglevel", "error", "-loop", "1", "-i", str(source),
                   "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT},format=yuv420p", "-t", str(duration_seconds), "-r", "30",
                   "-c:v", "libx264", "-movflags", "+faststart", "-y", str(target)]
        subprocess.run(command, check=True, timeout=50, capture_output=True)
        payload = target.read_bytes()
        if len(payload) < 1024:
            raise RuntimeError("VIDEO_RENDER_EMPTY")
        return payload
