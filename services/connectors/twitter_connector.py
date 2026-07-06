"""
Twitter/X Connector — wraps Twitter API behind the SocialConnector protocol.

Uses OAuth 2.0 Bearer token (same as the existing TwitterPlugin).
Supports text and media tweets with smart 280-char truncation.
"""

from __future__ import annotations

import logging
import time
from typing import Any, List, Optional, TYPE_CHECKING

import requests

from services.connectors.connector import (
    SocialConnector, ConnectorResult, ConnectorHealth,
    Comment, Message, Platform,
)

if TYPE_CHECKING:
    from models.social_account import SocialAccount

logger = logging.getLogger("kanyoza.connectors.twitter")

_API_BASE = "https://api.twitter.com/2"
_UPLOAD_BASE = "https://upload.twitter.com/1.1"
_TIMEOUT = 15


class TwitterConnector:
    """SocialConnector implementation for Twitter/X."""

    platform = Platform.TWITTER.value

    def __init__(self, social_account: Optional["SocialAccount"] = None) -> None:
        self._account = social_account
        if social_account:
            self._token = social_account.access_token or ""
        else:
            from config.settings import settings
            self._token = getattr(settings, "twitter_access_token", "")

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    # ── Publish ───────────────────────────────────────────────────────

    def publish(
        self,
        content: str,
        image_bytes: Optional[bytes] = None,
        image_url: Optional[str] = None,
        **kwargs: Any,
    ) -> ConnectorResult:
        if not self._token:
            return ConnectorResult(ok=False, platform=self.platform, error="Not configured")

        tweet_text = self._smart_truncate(content)

        try:
            media_ids = []
            if image_bytes:
                mid = self._upload_media(image_bytes)
                if mid:
                    media_ids.append(mid)

            payload: dict[str, Any] = {"text": tweet_text}
            if media_ids:
                payload["media"] = {"media_ids": media_ids}

            resp = requests.post(
                f"{_API_BASE}/tweets",
                headers=self._headers,
                json=payload,
                timeout=_TIMEOUT,
            )

            if resp.status_code == 201:
                tweet_id = resp.json().get("data", {}).get("id")
                logger.info("Tweet posted: %s (%d chars)", tweet_id, len(tweet_text))
                return ConnectorResult(ok=True, platform=self.platform, post_id=tweet_id)

            logger.error("Twitter publish failed: %s %s", resp.status_code, resp.text[:200])
            return ConnectorResult(
                ok=False, platform=self.platform,
                error=f"HTTP {resp.status_code}",
            )
        except Exception as exc:
            logger.error("TwitterConnector.publish: %s", exc)
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc))

    def _upload_media(self, image_bytes: bytes) -> Optional[str]:
        try:
            resp = requests.post(
                f"{_UPLOAD_BASE}/media/upload.json",
                headers={"Authorization": f"Bearer {self._token}"},
                files={"media": image_bytes},
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json().get("media_id_string")
            logger.error("Twitter media upload failed: %s", resp.text[:200])
        except Exception as exc:
            logger.error("Twitter media upload error: %s", exc)
        return None

    @staticmethod
    def _smart_truncate(content: str, max_len: int = 280) -> str:
        lines = content.split("\n")
        parts: list[str] = []
        char_count = 0
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if parts:
                    parts.append("")
                continue
            if stripped.startswith("#"):
                break
            if char_count + len(stripped) + 1 <= max_len - 3:
                parts.append(stripped)
                char_count += len(stripped) + 1
            else:
                break
        text = "\n".join(parts).strip()
        if len(text) > max_len:
            text = text[: max_len - 3] + "..."
        return text

    # ── Delete / Edit ─────────────────────────────────────────────────

    def delete(self, post_id: str) -> ConnectorResult:
        try:
            resp = requests.delete(
                f"{_API_BASE}/tweets/{post_id}",
                headers=self._headers,
                timeout=_TIMEOUT,
            )
            return ConnectorResult(ok=resp.status_code == 200, platform=self.platform, post_id=post_id)
        except Exception as exc:
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc))

    def edit(self, post_id: str, content: str) -> ConnectorResult:
        # Twitter API v2 does not support tweet editing for most apps
        return ConnectorResult(
            ok=False, platform=self.platform,
            error="Twitter API does not support editing tweets via v2 for most apps",
        )

    # ── Health ────────────────────────────────────────────────────────

    def health(self) -> ConnectorHealth:
        if not self._token:
            return ConnectorHealth(ok=False, platform=self.platform, error="Not configured")
        t0 = time.monotonic()
        try:
            resp = requests.get(
                f"{_API_BASE}/users/me",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=10,
            )
            ms = (time.monotonic() - t0) * 1000
            return ConnectorHealth(ok=resp.status_code == 200, platform=self.platform, latency_ms=round(ms, 1))
        except Exception as exc:
            ms = (time.monotonic() - t0) * 1000
            return ConnectorHealth(ok=False, platform=self.platform, latency_ms=round(ms, 1), error=str(exc))

    def validate(self) -> bool:
        return self.health().ok

    # ── Comments & Messages ───────────────────────────────────────────

    def fetch_comments(self, post_id: str, limit: int = 50) -> List[Comment]:
        # Twitter uses "quote tweets" and "replies" — not a simple comments API
        return []

    def fetch_messages(self, limit: int = 50) -> List[Message]:
        return []
