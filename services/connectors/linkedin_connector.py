"""
LinkedIn Connector — wraps LinkedIn API behind the SocialConnector protocol.

Uses OAuth 2.0 Bearer token. Supports text posts via UGC API.
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

logger = logging.getLogger("kanyoza.connectors.linkedin")

_API_BASE = "https://api.linkedin.com/v2"
_TIMEOUT = 15


class LinkedInConnector:
    """SocialConnector implementation for LinkedIn."""

    platform = Platform.LINKEDIN.value

    def __init__(self, social_account: Optional["SocialAccount"] = None) -> None:
        self._account = social_account
        if social_account:
            self._token = social_account.access_token or ""
            self._person_urn = social_account.provider_account_id or ""
        else:
            from config.settings import settings
            self._token = getattr(settings, "linkedin_access_token", "")
            self._person_urn = getattr(settings, "linkedin_person_urn", "")

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }

    # ── Publish ───────────────────────────────────────────────────────

    def publish(
        self,
        content: str,
        image_bytes: Optional[bytes] = None,
        image_url: Optional[str] = None,
        **kwargs: Any,
    ) -> ConnectorResult:
        if not self._token or not self._person_urn:
            return ConnectorResult(ok=False, platform=self.platform, error="Not configured")

        try:
            payload = {
                "author": self._person_urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": content[:3000]},
                        "shareMediaCategory": "NONE",
                    }
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC",
                },
            }

            resp = requests.post(
                f"{_API_BASE}/ugcPosts",
                headers=self._headers,
                json=payload,
                timeout=_TIMEOUT,
            )

            if resp.status_code == 201:
                post_id = resp.headers.get("X-RestLi-Id") or resp.json().get("id", "")
                logger.info("LinkedIn post published: %s", post_id)
                return ConnectorResult(ok=True, platform=self.platform, post_id=post_id)

            logger.error("LinkedIn publish failed: %s %s", resp.status_code, resp.text[:200])
            return ConnectorResult(
                ok=False, platform=self.platform,
                error=f"HTTP {resp.status_code}",
            )
        except Exception as exc:
            logger.error("LinkedInConnector.publish: %s", exc)
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc))

    # ── Delete / Edit ─────────────────────────────────────────────────

    def delete(self, post_id: str) -> ConnectorResult:
        try:
            resp = requests.delete(
                f"{_API_BASE}/ugcPosts/{post_id}",
                headers=self._headers,
                timeout=_TIMEOUT,
            )
            return ConnectorResult(ok=resp.status_code in (200, 204), platform=self.platform, post_id=post_id)
        except Exception as exc:
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc))

    def edit(self, post_id: str, content: str) -> ConnectorResult:
        # LinkedIn UGC API doesn't support editing after publish
        return ConnectorResult(
            ok=False, platform=self.platform,
            error="LinkedIn UGC API does not support editing published posts",
        )

    # ── Health ────────────────────────────────────────────────────────

    def health(self) -> ConnectorHealth:
        if not self._token:
            return ConnectorHealth(ok=False, platform=self.platform, error="Not configured")
        t0 = time.monotonic()
        try:
            resp = requests.get(
                f"{_API_BASE}/me",
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

    def fetch_comments(self, post_id: str, limit: int = 50) -> List[Comment]:
        return []

    def fetch_messages(self, limit: int = 50) -> List[Message]:
        return []
