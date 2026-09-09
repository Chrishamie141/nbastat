"""Official X user-context client with media and null-aware metrics support."""
from __future__ import annotations

import os
import re
import time

import requests


class OfficialX:
    api = "https://api.x.com"

    def __init__(self, session=None):
        if session is None:
            from requests_oauthlib import OAuth1
            names = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
            credentials = [os.getenv(name) for name in names]
            if not all(credentials):
                raise ValueError("Four OAuth 1.0a user-context credentials are required")
            session = requests.Session(); session.auth = OAuth1(*credentials)
        self.session = session
        self._followers = None

    def verify_company(self):
        expected = os.getenv("X_EXPECTED_USER_ID")
        if not expected:
            raise ValueError("X_EXPECTED_USER_ID is required")
        response = self.session.get(
            f"{self.api}/2/users/me",
            params={"user.fields": "public_metrics"},
            timeout=15,
            allow_redirects=False,
        )
        if response.status_code != 200 or response.json().get("data", {}).get("id") != expected:
            raise ValueError("X company-account verification failed")
        self._followers = (response.json().get("data", {}).get("public_metrics") or {}).get("followers_count")
        return {"user_id": expected, "verified": True}

    def upload_image(self, payload: bytes, content_type: str = "image/png", alt_text: str | None = None) -> str:
        if not payload or len(payload) > 5 * 1024 * 1024:
            raise ValueError("X image payload must be between 1 byte and 5 MB")
        response = self.session.post(
            f"{self.api}/2/media/upload",
            data={"media_category": "tweet_image", "media_type": content_type},
            files={"media": ("smartbets.png", payload, content_type)},
            timeout=35,
            allow_redirects=False,
        )
        if response.status_code not in {200, 201, 202}:
            raise RuntimeError(f"X_MEDIA_HTTP_{response.status_code}")
        media_id = self._media_id(response)
        if not media_id:
            raise RuntimeError("X_MEDIA_MISSING_ID")
        if alt_text:
            metadata = self.session.post(
                f"{self.api}/2/media/metadata",
                json={"id": media_id, "metadata": {"alt_text": {"text": alt_text[:1000]}}},
                timeout=15,
                allow_redirects=False,
            )
            if metadata.status_code not in {200, 201, 204}:
                raise RuntimeError(f"X_MEDIA_METADATA_HTTP_{metadata.status_code}")
        return media_id

    def upload_video(self, payload: bytes, content_type: str = "video/mp4") -> str:
        if not payload or len(payload) > 512 * 1024 * 1024:
            raise ValueError("X video payload must be between 1 byte and 512 MB")
        init = self.session.post(
            f"{self.api}/2/media/upload/initialize",
            json={
                "total_bytes": len(payload),
                "media_type": content_type,
                "media_category": "tweet_video",
                "shared": False,
            },
            timeout=20,
            allow_redirects=False,
        )
        if init.status_code not in {200, 201, 202}:
            raise RuntimeError(f"X_VIDEO_INIT_HTTP_{init.status_code}")
        media_id = self._media_id(init)
        if not media_id:
            raise RuntimeError("X_MEDIA_MISSING_ID")
        for index, offset in enumerate(range(0, len(payload), 4 * 1024 * 1024)):
            append = self.session.post(
                f"{self.api}/2/media/upload/{media_id}/append",
                data={"segment_index": index},
                files={"media": ("segment", payload[offset:offset + 4 * 1024 * 1024], content_type)},
                timeout=35,
                allow_redirects=False,
            )
            if append.status_code not in {200, 201, 204}:
                raise RuntimeError(f"X_VIDEO_APPEND_HTTP_{append.status_code}")
        finalize = self.session.post(
            f"{self.api}/2/media/upload/{media_id}/finalize",
            timeout=20,
            allow_redirects=False,
        )
        if finalize.status_code not in {200, 201, 202}:
            raise RuntimeError(f"X_VIDEO_FINALIZE_HTTP_{finalize.status_code}")
        processing = (finalize.json().get("data") or {}).get("processing_info") or {}
        for _ in range(4):
            state = processing.get("state")
            if state in (None, "succeeded"):
                return media_id
            if state == "failed":
                raise RuntimeError("X_VIDEO_PROCESSING_FAILED")
            time.sleep(min(3, max(0, int(processing.get("check_after_secs") or 1))))
            status = self.session.get(
                f"{self.api}/2/media/upload",
                params={"media_id": media_id},
                timeout=15,
                allow_redirects=False,
            )
            if status.status_code != 200:
                raise RuntimeError(f"X_VIDEO_STATUS_HTTP_{status.status_code}")
            processing = (status.json().get("data") or {}).get("processing_info") or {}
        raise RuntimeError("X_VIDEO_PROCESSING_TIMEOUT")

    @staticmethod
    def _media_id(response) -> str:
        """Read a v2 media ID without ever coercing it through a numeric type."""
        return str((response.json().get("data") or {}).get("id") or "")

    def upload(self, payload: bytes, content_type: str, alt_text: str | None = None) -> str:
        return self.upload_video(payload, content_type) if content_type.startswith("video/") else self.upload_image(payload, content_type, alt_text)

    def post(self, content: str, media_ids: list[str] | None = None, reply_to: str | None = None):
        body = {"text": content}
        if media_ids:
            body["media"] = {"media_ids": media_ids[:4]}
        if reply_to:
            if not re.fullmatch(r"[0-9]+", reply_to):
                raise ValueError("Invalid X reply post ID")
            body["reply"] = {"in_reply_to_tweet_id": reply_to}
        return self.session.post(f"{self.api}/2/tweets", json=body, timeout=20, allow_redirects=False)

    def lookup(self, post_id: str):
        if not re.fullmatch(r"[0-9]+", post_id):
            raise ValueError("Invalid X post ID")
        return self.session.get(f"{self.api}/2/tweets/{post_id}",
                                params={"tweet.fields": "author_id,text,public_metrics,non_public_metrics,organic_metrics"},
                                timeout=15, allow_redirects=False)

    def metrics(self, post_id: str) -> dict:
        response = self.lookup(post_id)
        if response.status_code != 200:
            raise RuntimeError(f"X_METRICS_HTTP_{response.status_code}")
        data = response.json().get("data", {})
        public = data.get("public_metrics") or {}
        private = data.get("non_public_metrics") or data.get("organic_metrics") or {}
        return {
            "impressions": private.get("impression_count", public.get("impression_count")), "likes": public.get("like_count"),
            "replies": public.get("reply_count"), "reposts": public.get("retweet_count"),
            "quotes": public.get("quote_count"), "bookmarks": public.get("bookmark_count"),
            "url_clicks": private.get("url_link_clicks"), "profile_clicks": private.get("user_profile_clicks"),
            "followers": self._followers,
        }
