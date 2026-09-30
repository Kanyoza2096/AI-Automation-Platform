"""
WhatsApp Connector — wraps WhatsApp Business Cloud API behind the
SocialConnector protocol.

Uses the Meta WhatsApp Cloud API. Publishing here means sending a
message to a specific number or broadcast list — WhatsApp doesn't have
a "feed" like other platforms.

The ``social_account.access_token`` is the WhatsApp Business API token.
The ``social_account.provider_account_id`` is the Phone Number ID.
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

logger = logging.getLogger("kanyoza.connectors.whatsapp")

_GRAPH_BASE = "https://graph.facebook.com/v22.0"
_TIMEOUT = 15


class WhatsAppConnector:
    """SocialConnector implementation for WhatsApp Business.

    Note: WhatsApp is primarily a messaging platform, not a feed.
    ``publish()`` sends a message to a configured broadcast number.
    """

    platform = Platform.WHATSAPP.value

    NATIVE_IDEMPOTENCY = False
    SAFE_RETRY_AFTER_AMBIGUOUS = False

    def __init__(self, social_account: Optional["SocialAccount"] = None) -> None:
        self._account = social_account
        if social_account:
            self._token = social_account.access_token or ""
            self._phone_id = social_account.provider_account_id or ""
        else:
            from config.settings import settings
            self._token = getattr(settings, "whatsapp_token", "")
            self._phone_id = getattr(settings, "whatsapp_phone_id", "")

    @property
    def _base(self) -> str:
        return f"{_GRAPH_BASE}/{self._phone_id}"

    # ── Publish ───────────────────────────────────────────────────────

    def publish(
        self,
        content: str,
        image_bytes: Optional[bytes] = None,
        image_url: Optional[str] = None,
        to_number: Optional[str] = None,
        **kwargs: Any,
    ) -> ConnectorResult:
        """Send a WhatsApp text or image message. ``to_number`` is required."""
        if not self._token or not self._phone_id:
            return ConnectorResult(ok=False, platform=self.platform, error="Not configured", outcome="failed")

        recipient = to_number or kwargs.get("recipient")
        if not recipient:
            return ConnectorResult(ok=False, platform=self.platform, error="WhatsApp requires a 'to_number' or 'recipient' kwarg", outcome="failed")
        if image_bytes and not image_url:
            return ConnectorResult(ok=False, platform=self.platform, error="WhatsApp media requires a publicly accessible image_url; raw bytes are not silently dropped", outcome="failed")

        if image_url:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": recipient,
                "type": "image",
                "image": {"link": image_url, "caption": content[:1024]},
            }
        else:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": recipient,
                "type": "text",
                "text": {"preview_url": False, "body": content[:4096]},
            }

        try:
            resp = requests.post(
                f"{self._base}/messages",
                headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
                json=payload,
                timeout=_TIMEOUT,
            )
            if resp.status_code == 200:
                msg_id = (resp.json().get("messages") or [{}])[0].get("id", "")
                logger.info("WhatsApp message sent to %s: %s", recipient, msg_id)
                return ConnectorResult(ok=True, platform=self.platform, post_id=msg_id)
            return ConnectorResult(ok=False, platform=self.platform, error=f"HTTP {resp.status_code}", outcome=("failed" if 400 <= resp.status_code < 500 else "uncertain"))
        except requests.Timeout as exc:
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc), outcome="uncertain")
        except requests.RequestException as exc:
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc), outcome="uncertain")
        except Exception as exc:
            logger.error("WhatsAppConnector.publish: %s", exc)
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc), outcome="failed")

    def lookup_post(self, post_id: str):
        """Return None: WhatsApp outbound message IDs are reconciled via webhooks, not GET lookup."""
        return None

    # ── Delete / Edit ─────────────────────────────────────────────────

    def delete(self, post_id: str) -> ConnectorResult:
        return ConnectorResult(
            ok=False, platform=self.platform,
            error="WhatsApp does not support programmatic message deletion",
        )

    def edit(self, post_id: str, content: str) -> ConnectorResult:
        return ConnectorResult(
            ok=False, platform=self.platform,
            error="WhatsApp does not support editing sent messages",
        )

    # ── Health ────────────────────────────────────────────────────────

    def health(self) -> ConnectorHealth:
        if not self._token or not self._phone_id:
            return ConnectorHealth(ok=False, platform=self.platform, error="Not configured")
        t0 = time.monotonic()
        try:
            resp = requests.get(
                f"{self._base}",
                params={"fields": "id,display_phone_number", "access_token": self._token},
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
        return []  # WhatsApp is not a feed-based platform

    def fetch_messages(self, limit: int = 50) -> List[Message]:
        # WhatsApp webhook delivers messages; we don't poll for them
        return []
