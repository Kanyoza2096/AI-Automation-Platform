"""
Facebook Connector — wraps FacebookService behind the SocialConnector protocol.

Accepts a SocialAccount at construction time so each connector instance
is bound to a specific page/token pair.
"""

from __future__ import annotations

import logging
import time
from typing import Any, List, Optional, TYPE_CHECKING

from services.connectors.connector import (
    SocialConnector, ConnectorResult, ConnectorHealth,
    Comment, Message, Platform,
)

if TYPE_CHECKING:
    from models.social_account import SocialAccount
    from services.facebook import FacebookService

logger = logging.getLogger("kanyoza.connectors.facebook")


class FacebookConnector:
    """SocialConnector implementation for Facebook Pages."""

    platform = Platform.FACEBOOK.value

    NATIVE_IDEMPOTENCY = False
    SAFE_RETRY_AFTER_AMBIGUOUS = False

    def __init__(
        self,
        facebook_service: "FacebookService",
        social_account: Optional["SocialAccount"] = None,
    ) -> None:
        self._svc = facebook_service
        self._account = social_account

        # Resolve credentials: account-specific or service defaults
        if social_account:
            self._page_id = social_account.platform_account_id
            self._page_token = social_account.access_token or ""
        else:
            self._page_id = self._svc.page_id
            self._page_token = self._svc.page_token

    # ── Publish ───────────────────────────────────────────────────────

    def publish(
        self,
        content: str,
        image_bytes: Optional[bytes] = None,
        image_url: Optional[str] = None,
        **kwargs: Any,
    ) -> ConnectorResult:
        try:
            result = self._svc.post_to_page(
                message=content,
                image_bytes=image_bytes,
                image_url=image_url if not image_bytes else None,
                page_id=self._page_id,
                page_token=self._page_token,
            )
            # post_to_page always returns a structured dict: {"ok": bool, ...}
            if result.get("ok"):
                return ConnectorResult(
                    ok=True,
                    platform=self.platform,
                    post_id=result.get("post_id"),
                )
            error_info = result.get("error", {})
            error_msg = (
                error_info.get("message", "Unknown error")
                if isinstance(error_info, dict)
                else str(error_info)
            )
            logger.error(
                "FacebookConnector.publish failed — stage=%s message=%s",
                error_info.get("stage", "unknown") if isinstance(error_info, dict) else "unknown",
                error_msg,
            )
            return ConnectorResult(ok=False, platform=self.platform, error=error_msg)
        except Exception as exc:
            logger.error("FacebookConnector.publish: %s", exc)
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc))

    def lookup_post(self, post_id: str):
        try:
            resp = self._svc._session.get(f"https://graph.facebook.com/v22.0/{post_id}", params={"fields": "id", "access_token": self._page_token}, timeout=15)
            if resp.status_code == 200 and resp.json().get("id"):
                return True
            if resp.status_code == 404:
                return False
            return None
        except Exception:
            return None

    # ── Delete / Edit ─────────────────────────────────────────────────

    def delete(self, post_id: str) -> ConnectorResult:
        """Delete a Facebook post by ID."""
        try:
            from utils.http_client import HttpClient
            resp = self._svc._session.delete(
                f"https://graph.facebook.com/v22.0/{post_id}",
                params={"access_token": self._page_token},
                timeout=15,
            )
            ok = resp.status_code == 200
            return ConnectorResult(ok=ok, platform=self.platform, post_id=post_id)
        except Exception as exc:
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc))

    def edit(self, post_id: str, content: str) -> ConnectorResult:
        """Edit a Facebook post's message."""
        try:
            resp = self._svc._session.post(
                f"https://graph.facebook.com/v22.0/{post_id}",
                data={"message": content, "access_token": self._page_token},
                timeout=15,
            )
            ok = resp.status_code == 200
            return ConnectorResult(ok=ok, platform=self.platform, post_id=post_id)
        except Exception as exc:
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc))

    # ── Health ────────────────────────────────────────────────────────

    def health(self) -> ConnectorHealth:
        t0 = time.monotonic()
        try:
            result = self._svc.validate_token()
            ok = result.get("ok", False) if isinstance(result, dict) else bool(result)
            ms = (time.monotonic() - t0) * 1000
            return ConnectorHealth(ok=ok, platform=self.platform, latency_ms=round(ms, 1))
        except Exception as exc:
            ms = (time.monotonic() - t0) * 1000
            return ConnectorHealth(ok=False, platform=self.platform, latency_ms=round(ms, 1), error=str(exc))

    def validate(self) -> bool:
        result = self._svc.validate_token()
        return result.get("ok", False) if isinstance(result, dict) else bool(result)

    # ── Comments & Messages ───────────────────────────────────────────

    def fetch_comments(self, post_id: str, limit: int = 50) -> List[Comment]:
        raw = self._svc.get_unreplied_comments(post_id, max_fetch=limit)
        return [
            Comment(
                id=c.get("id", ""),
                post_id=post_id,
                author_id=c.get("from", {}).get("id", ""),
                author_name=c.get("from", {}).get("name", ""),
                text=c.get("message", ""),
                created_at=c.get("created_time"),
                platform=self.platform,
            )
            for c in raw
        ]

    def fetch_messages(self, limit: int = 50) -> List[Message]:
        # Messenger inbox requires special permissions; placeholder
        return []
