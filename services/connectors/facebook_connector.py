"""
Facebook Connector — wraps FacebookService behind the SocialConnector protocol.

Accepts a SocialAccount at construction time so each connector instance
is bound to a specific page/token pair.

Receipt contract (v2):
  - A publish is only classified as `ok=True` when the provider returns a
    concrete post ID. `ok=True` without a post ID is a fabricated success and
    is downgraded to `outcome="uncertain"`.
  - The connector surfaces `page_id` and `account_id` on every result so the
    caller can persist them for reconciliation and audit.
  - Never store or return the page token.
"""

from __future__ import annotations

import logging
import time
from typing import Any, List, Optional, TYPE_CHECKING

from services.connectors.capabilities import ConnectorCapabilities
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

    # ── Internal helpers ──────────────────────────────────────────────

    @property
    def _account_id(self) -> Optional[str]:
        """Stable identifier for this SocialAccount (never the token)."""
        return getattr(self._account, "id", None) if self._account else None

    def _receipt(
        self,
        ok: bool,
        post_id: Optional[str] = None,
        error: Optional[Any] = None,
        outcome: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> ConnectorResult:
        """Build a ConnectorResult carrying receipt metadata.

        The result always advertises:
          - page_id   (which page the request was addressed to)
          - account_id (which social_account row authorized it)
          - outcome   ("succeeded" | "failed" | "uncertain")
        without ever including the page token.
        """
        result = ConnectorResult(
            ok=ok,
            platform=self.platform,
            post_id=post_id,
            error=error,
        )
        # Attach metadata without changing the dataclass contract. Callers
        # read these fields opportunistically; older callers ignore them.
        try:
            result.page_id = self._page_id
            result.account_id = self._account_id
            if outcome is not None:
                result.outcome = outcome
            elif ok and post_id:
                result.outcome = "succeeded"
            elif ok and not post_id:
                result.outcome = "uncertain"
            else:
                result.outcome = "failed"
            if extra:
                result.receipt = extra
        except Exception:
            # ConnectorResult may be frozen; fall through with best effort.
            pass
        return result

    def send_message(self, recipient_psid: str, message: str, **kwargs: Any) -> ConnectorResult:
        """Send a Messenger message using this workspace-owned Page account."""
        if not self._page_token or not self._page_id:
            return self._receipt(False, error="Facebook account is not configured", outcome="failed")
        recipient = str(recipient_psid or "").strip()
        text = str(message or "").strip()
        if not recipient or not text:
            return self._receipt(False, error="recipient_psid and message are required", outcome="failed")
        try:
            result = self._svc.send_message_with_credentials(
                recipient_psid=recipient, message=text, page_token=self._page_token
            )
            message_id = str(result.get("message_id") or "").strip() if isinstance(result, dict) else ""
            if message_id:
                return self._receipt(True, post_id=message_id, outcome="succeeded")
            return self._receipt(False, error="Facebook accepted message without message_id", outcome="uncertain")
        except Exception as exc:
            return self._receipt(False, error=str(exc), outcome="uncertain")

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

            if not isinstance(result, dict):
                return self._receipt(
                    ok=False,
                    error=f"Unexpected provider response type: {type(result).__name__}",
                    outcome="uncertain",
                )

            provider_ok = bool(result.get("ok"))
            post_id = result.get("post_id")

            # ── Receipt contract ──────────────────────────────────
            # Success requires a concrete post ID. `ok=True` without one is
            # a fabricated success from the provider layer and MUST be
            # downgraded to uncertain — never silently accepted.
            if provider_ok and post_id:
                logger.info(
                    "FacebookConnector.publish OK page=%s account=%s post_id=%s",
                    self._page_id, self._account_id, post_id,
                )
                return self._receipt(ok=True, post_id=post_id, outcome="succeeded")

            if provider_ok and not post_id:
                logger.warning(
                    "FacebookConnector.publish AMBIGUOUS — provider ok without "
                    "post_id page=%s account=%s raw=%r",
                    self._page_id, self._account_id, result,
                )
                return self._receipt(
                    ok=False,
                    post_id=None,
                    outcome="uncertain",
                    error="Provider returned success without a post ID",
                    extra={"provider_response": result, "reason": "no_receipt"},
                )

            error_info = result.get("error", {})
            error_msg = (
                error_info.get("message", "Unknown error")
                if isinstance(error_info, dict)
                else str(error_info)
            )
            logger.error(
                "FacebookConnector.publish FAILED page=%s account=%s stage=%s message=%s",
                self._page_id, self._account_id,
                error_info.get("stage", "unknown") if isinstance(error_info, dict) else "unknown",
                error_msg,
            )
            return self._receipt(
                ok=False,
                error=error_msg,
                outcome="failed",
                extra={"provider_response": result},
            )

        except Exception as exc:
            # Timeouts / transport errors are inherently ambiguous for
            # external side effects. Do not claim either success or failure.
            logger.exception(
                "FacebookConnector.publish EXCEPTION page=%s account=%s",
                self._page_id, self._account_id,
            )
            return self._receipt(
                ok=False,
                error=str(exc),
                outcome="uncertain",
                extra={"exception_type": type(exc).__name__},
            )

    def lookup_post(self, post_id: str):
        try:
            resp = self._svc._session.get(
                f"https://graph.facebook.com/v22.0/{post_id}",
                params={"fields": "id", "access_token": self._page_token},
                timeout=15,
            )
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
            resp = self._svc._session.delete(
                f"https://graph.facebook.com/v22.0/{post_id}",
                params={"access_token": self._page_token},
                timeout=15,
            )
            ok = resp.status_code == 200
            return self._receipt(
                ok=ok,
                post_id=post_id if ok else None,
                outcome="succeeded" if ok else "failed",
                error=None if ok else f"HTTP {resp.status_code}",
            )
        except Exception as exc:
            return self._receipt(ok=False, error=str(exc), outcome="uncertain")

    def edit(self, post_id: str, content: str) -> ConnectorResult:
        """Edit a Facebook post's message."""
        try:
            resp = self._svc._session.post(
                f"https://graph.facebook.com/v22.0/{post_id}",
                data={"message": content, "access_token": self._page_token},
                timeout=15,
            )
            ok = resp.status_code == 200
            return self._receipt(
                ok=ok,
                post_id=post_id if ok else None,
                outcome="succeeded" if ok else "failed",
                error=None if ok else f"HTTP {resp.status_code}",
            )
        except Exception as exc:
            return self._receipt(ok=False, error=str(exc), outcome="uncertain")

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


# V69.7 certification metadata: explicit provider semantics, not inferred behavior.
FacebookConnector.CAPABILITIES = ConnectorCapabilities(
    native_idempotency=False,
    authoritative_lookup=True,
    safe_retry_after_ambiguous=False,
)
FacebookConnector.RATE_LIMIT_POLICY = {
    "strategy": "provider_response_backoff",
    "reconcile_after_ambiguous": True,
}
FacebookConnector.REVOCATION_POLICY = (
    "validate_token_before_activation_and_on_revalidation"
)
