"""
Instagram Connector — publishes via Facebook Graph API (Instagram is managed
through Facebook Business pages).

Uses the Instagram Content Publishing API which requires:
1. An Instagram Business/Creator account linked to a Facebook Page.
2. A Facebook Page Access Token with `instagram_basic` and
   `instagram_content_publish` permissions.
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

logger = logging.getLogger("kanyoza.connectors.instagram")

_GRAPH_BASE = "https://graph.facebook.com/v22.0"
_TIMEOUT = 30


class InstagramConnector:
    """SocialConnector implementation for Instagram (via Facebook Graph API).

    The ``social_account.provider_account_id`` should be the Instagram
    Business Account ID (not the Facebook Page ID).
    ``social_account.access_token`` is the Facebook Page Access Token
    that has Instagram permissions.
    """

    platform = Platform.INSTAGRAM.value

    NATIVE_IDEMPOTENCY = False
    SAFE_RETRY_AFTER_AMBIGUOUS = False

    def __init__(self, social_account: Optional["SocialAccount"] = None) -> None:
        self._account = social_account
        if social_account:
            self._ig_account_id = social_account.provider_account_id or ""
            self._token = social_account.access_token or ""
        else:
            self._ig_account_id = ""
            self._token = ""

    # ── Publish ───────────────────────────────────────────────────────

    def publish(
        self,
        content: str,
        image_bytes: Optional[bytes] = None,
        image_url: Optional[str] = None,
        **kwargs: Any,
    ) -> ConnectorResult:
        """Publish an image post to Instagram.

        Instagram requires an ``image_url`` (publicly accessible URL).
        If only ``image_bytes`` are provided, the caller must first upload
        the image somewhere and pass the URL.
        """
        if not self._token or not self._ig_account_id:
            return ConnectorResult(ok=False, platform=self.platform, error="Not configured", outcome="failed")

        if not image_url:
            return ConnectorResult(
                ok=False, platform=self.platform,
                error="Instagram requires a public image_url for publishing", outcome="failed",
            )

        try:
            # Step 1: Create media container
            container_resp = requests.post(
                f"{_GRAPH_BASE}/{self._ig_account_id}/media",
                params={
                    "image_url": image_url,
                    "caption": content[:2200],
                    "access_token": self._token,
                },
                timeout=_TIMEOUT,
            )
            if container_resp.status_code != 200:
                return ConnectorResult(
                    ok=False, platform=self.platform,
                    error=f"Container creation failed: HTTP {container_resp.status_code}", outcome=("failed" if 400 <= container_resp.status_code < 500 else "uncertain"),
                )

            container_id = container_resp.json().get("id")
            if not container_id:
                return ConnectorResult(ok=False, platform=self.platform, error="No container ID returned", outcome="failed")

            # The container may need processing before it can be published.
            # Poll briefly and fail closed if processing never reaches FINISHED.
            deadline = time.monotonic() + float(kwargs.get("container_timeout", 60))
            while time.monotonic() < deadline:
                status_resp = requests.get(
                    f"{_GRAPH_BASE}/{container_id}",
                    params={"fields": "status_code", "access_token": self._token},
                    timeout=_TIMEOUT,
                )
                if status_resp.status_code >= 500:
                    return ConnectorResult(ok=False, platform=self.platform, error=f"Container status unavailable: HTTP {status_resp.status_code}", outcome="uncertain")
                if 400 <= status_resp.status_code < 500:
                    return ConnectorResult(ok=False, platform=self.platform, error=f"Container status rejected: HTTP {status_resp.status_code}", outcome="failed")
                status = status_resp.json().get("status_code")
                if status in ("FINISHED", "PUBLISHED"):
                    break
                if status in ("ERROR", "EXPIRED"):
                    return ConnectorResult(ok=False, platform=self.platform, error=f"Instagram container failed: {status}", outcome="failed")
                time.sleep(1)
            else:
                return ConnectorResult(ok=False, platform=self.platform, error="Instagram container processing timed out", outcome="uncertain")

            # Step 2: Publish the container
            publish_resp = requests.post(
                f"{_GRAPH_BASE}/{self._ig_account_id}/media_publish",
                params={
                    "creation_id": container_id,
                    "access_token": self._token,
                },
                timeout=_TIMEOUT,
            )

            if publish_resp.status_code == 200:
                post_id = publish_resp.json().get("id", "")
                logger.info("Instagram post published: %s", post_id)
                return ConnectorResult(ok=True, platform=self.platform, post_id=post_id)

            logger.error("Instagram publish failed: %s %s", publish_resp.status_code, publish_resp.text[:200])
            return ConnectorResult(
                ok=False, platform=self.platform,
                error=f"Publish failed: HTTP {publish_resp.status_code}", outcome=("failed" if 400 <= publish_resp.status_code < 500 else "uncertain"),
            )

        except Exception as exc:
            logger.error("InstagramConnector.publish: %s", exc)
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc))

    def lookup_post(self, post_id: str):
        if not self._token:
            return None
        try:
            resp = requests.get(f"{_GRAPH_BASE}/{post_id}", params={"fields": "id", "access_token": self._token}, timeout=_TIMEOUT)
            if resp.status_code == 200 and resp.json().get("id"):
                return True
            if resp.status_code == 404:
                return False
            return None
        except Exception:
            return None

    # ── Delete / Edit ─────────────────────────────────────────────────

    def delete(self, post_id: str) -> ConnectorResult:
        # Instagram API doesn't support deleting posts programmatically
        return ConnectorResult(
            ok=False, platform=self.platform,
            error="Instagram API does not support programmatic post deletion",
        )

    def edit(self, post_id: str, content: str) -> ConnectorResult:
        return ConnectorResult(
            ok=False, platform=self.platform,
            error="Instagram API does not support editing published posts",
        )

    # ── Health ────────────────────────────────────────────────────────

    def health(self) -> ConnectorHealth:
        if not self._token or not self._ig_account_id:
            return ConnectorHealth(ok=False, platform=self.platform, error="Not configured")
        t0 = time.monotonic()
        try:
            resp = requests.get(
                f"{_GRAPH_BASE}/{self._ig_account_id}",
                params={"fields": "id,username", "access_token": self._token},
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
        if not self._token:
            return []
        try:
            resp = requests.get(
                f"{_GRAPH_BASE}/{post_id}/comments",
                params={"fields": "id,text,username,timestamp", "limit": limit, "access_token": self._token},
                timeout=10,
            )
            if resp.status_code != 200:
                return []
            return [
                Comment(
                    id=c.get("id", ""),
                    post_id=post_id,
                    author_id="",
                    author_name=c.get("username", ""),
                    text=c.get("text", ""),
                    created_at=c.get("timestamp"),
                    platform=self.platform,
                )
                for c in resp.json().get("data", [])
            ]
        except Exception:
            return []

    def fetch_messages(self, limit: int = 50) -> List[Message]:
        return []
