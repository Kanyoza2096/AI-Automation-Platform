"""
Telegram Connector — uses Telegram Bot API for channel/group publishing.

The ``social_account.access_token`` should be the Bot Token.
The ``social_account.provider_account_id`` should be the chat_id
(channel username like @channel or numeric group ID).
"""

from __future__ import annotations

import io
import logging
import time
from typing import Any, List, Optional, TYPE_CHECKING

import requests
from utils.http_client import HttpClient

from services.connectors.connector import (
    SocialConnector, ConnectorResult, ConnectorHealth,
    Comment, Message, Platform,
)

if TYPE_CHECKING:
    from models.social_account import SocialAccount

logger = logging.getLogger("kanyoza.connectors.telegram")

_API_BASE = "https://api.telegram.org"
_TIMEOUT = 15


class TelegramConnector:
    """SocialConnector implementation for Telegram channels/groups."""

    platform = Platform.TELEGRAM.value

    NATIVE_IDEMPOTENCY = False
    SAFE_RETRY_AFTER_AMBIGUOUS = False

    def __init__(self, social_account: Optional["SocialAccount"] = None) -> None:
        self._http = HttpClient("telegram", default_timeout=30.0)
        self._account = social_account
        if social_account:
            self._bot_token = social_account.access_token or ""
            self._chat_id = getattr(social_account, 'platform_account_id', '') or getattr(social_account, 'provider_account_id', '') or ""
        else:
            from config.settings import settings
            self._bot_token = getattr(settings, "telegram_bot_token", "") or ""
            self._chat_id = getattr(settings, "telegram_chat_id", "") or ""
    @property
    def _base(self) -> str:
        return f"{_API_BASE}/bot{self._bot_token}"

    # ── Publish ───────────────────────────────────────────────────────

    def publish(
        self,
        content: str,
        image_bytes: Optional[bytes] = None,
        image_url: Optional[str] = None,
        **kwargs: Any,
    ) -> ConnectorResult:
        if not self._bot_token or not self._chat_id:
            return ConnectorResult(ok=False, platform=self.platform, error="Not configured", outcome="failed")

        try:
            if image_bytes:
                return self._send_photo(content, image_bytes)
            if image_url:
                return self._send_photo_url(content, image_url)
            return self._send_message(content)
        except requests.Timeout as exc:
            logger.error("TelegramConnector.publish timeout: %s", exc)
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc), outcome="uncertain")
        except requests.RequestException as exc:
            logger.error("TelegramConnector.publish transport error: %s", exc)
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc), outcome="uncertain")
        except Exception as exc:
            logger.error("TelegramConnector.publish: %s", exc)
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc), outcome="failed")

    def _send_message(self, text: str) -> ConnectorResult:
        resp = self._http.post(
            f"{self._base}/sendMessage",
            json={
                "chat_id": self._chat_id,
                "text": text[:4096],
                "parse_mode": "Markdown",
            },
            timeout=_TIMEOUT,
        )
        data = resp.json()
        if data.get("ok"):
            msg_id = str(data.get("result", {}).get("message_id", ""))
            logger.info("Telegram message sent: %s", msg_id)
            return ConnectorResult(ok=True, platform=self.platform, post_id=msg_id)
        status = getattr(resp, "status_code", 0)
        return ConnectorResult(ok=False, platform=self.platform, error=data.get("description", "Unknown error"), outcome=("failed" if 400 <= status < 500 else "uncertain"))

    def _send_photo(self, caption: str, image_bytes: bytes) -> ConnectorResult:
        resp = self._http.post(
            f"{self._base}/sendPhoto",
            data={"chat_id": self._chat_id, "caption": caption[:1024], "parse_mode": "Markdown"},
            files={"photo": ("post.png", io.BytesIO(image_bytes), "image/png")},
            timeout=30,
        )
        data = resp.json()
        if data.get("ok"):
            msg_id = str(data.get("result", {}).get("message_id", ""))
            logger.info("Telegram photo sent: %s", msg_id)
            return ConnectorResult(ok=True, platform=self.platform, post_id=msg_id)
        status = getattr(resp, "status_code", 0)
        return ConnectorResult(ok=False, platform=self.platform, error=data.get("description", "Unknown error"), outcome=("failed" if 400 <= status < 500 else "uncertain"))

    def _send_photo_url(self, caption: str, image_url: str) -> ConnectorResult:
        resp = self._http.post(
            f"{self._base}/sendPhoto",
            json={"chat_id": self._chat_id, "photo": image_url, "caption": caption[:1024], "parse_mode": "Markdown"},
            timeout=30,
        )
        data = resp.json()
        if data.get("ok"):
            msg_id = str(data.get("result", {}).get("message_id", ""))
            return ConnectorResult(ok=True, platform=self.platform, post_id=msg_id)
        status = getattr(resp, "status_code", 0)
        return ConnectorResult(ok=False, platform=self.platform, error=data.get("description", "Unknown error"), outcome=("failed" if 400 <= status < 500 else "uncertain"))

    # ── Delete / Edit ─────────────────────────────────────────────────

    def delete(self, post_id: str) -> ConnectorResult:
        try:
            resp = self._http.post(
                f"{self._base}/deleteMessage",
                json={"chat_id": self._chat_id, "message_id": int(post_id)},
                timeout=_TIMEOUT,
            )
            return ConnectorResult(ok=resp.json().get("ok", False), platform=self.platform, post_id=post_id)
        except Exception as exc:
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc))

    def edit(self, post_id: str, content: str) -> ConnectorResult:
        try:
            resp = self._http.post(
                f"{self._base}/editMessageText",
                json={
                    "chat_id": self._chat_id,
                    "message_id": int(post_id),
                    "text": content[:4096],
                    "parse_mode": "Markdown",
                },
                timeout=_TIMEOUT,
            )
            return ConnectorResult(ok=resp.json().get("ok", False), platform=self.platform, post_id=post_id)
        except Exception as exc:
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc))

    # ── Health ────────────────────────────────────────────────────────

    def health(self) -> ConnectorHealth:
        if not self._bot_token:
            return ConnectorHealth(ok=False, platform=self.platform, error="Not configured")
        t0 = time.monotonic()
        try:
            resp = self._http.get(f"{self._base}/getMe", timeout=10)
            ms = (time.monotonic() - t0) * 1000
            data = resp.json()
            return ConnectorHealth(
                ok=data.get("ok", False),
                platform=self.platform,
                latency_ms=round(ms, 1),
                metadata={"bot_username": data.get("result", {}).get("username", "")},
            )
        except Exception as exc:
            ms = (time.monotonic() - t0) * 1000
            return ConnectorHealth(ok=False, platform=self.platform, latency_ms=round(ms, 1), error=str(exc))

    def validate(self) -> bool:
        return self.health().ok

    def fetch_comments(self, post_id: str, limit: int = 50) -> List[Comment]:
        # Telegram channels don't have a standard comments API
        return []

    def fetch_messages(self, limit: int = 50) -> List[Message]:
        # Could use getUpdates, but that's for bot interactions, not publishing
        return []
