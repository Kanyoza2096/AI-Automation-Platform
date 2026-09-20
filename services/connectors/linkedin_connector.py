"""
LinkedIn Connector — wraps LinkedIn API behind the SocialConnector protocol.

Uses OAuth 2.0 Bearer token. Supports text and image posts via the current Posts + Images APIs.
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

_API_BASE = "https://api.linkedin.com/rest"
_DEFAULT_VERSION = "202608"
_TIMEOUT = 15


class LinkedInConnector:
    """SocialConnector implementation for LinkedIn."""

    platform = Platform.LINKEDIN.value

    NATIVE_IDEMPOTENCY = False
    SAFE_RETRY_AFTER_AMBIGUOUS = False

    def __init__(self, social_account: Optional["SocialAccount"] = None) -> None:
        self._account = social_account
        if social_account:
            self._token = social_account.access_token or ""
            self._person_urn = (getattr(social_account, "platform_account_id", "") or
                                getattr(social_account, "provider_account_id", ""))
            self._linkedin_version = str((getattr(social_account, "platform_config", {}) or {}).get("linkedin_version") or _DEFAULT_VERSION)
        else:
            from config.settings import settings
            self._token = getattr(settings, "linkedin_access_token", "")
            self._person_urn = getattr(settings, "linkedin_person_urn", "")
            self._linkedin_version = str(getattr(settings, "linkedin_version", _DEFAULT_VERSION))

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
            "Linkedin-Version": self._linkedin_version,
        }

    def _upload_image(self, image_bytes: bytes) -> tuple[Optional[str], Optional[str]]:
        """Upload an image through LinkedIn Images API and wait for availability.

        Returns (image_urn, error). The upload is provider-side state, so any
        transport/processing ambiguity is surfaced to the caller rather than
        silently publishing a text-only post.
        """
        try:
            init = requests.post(
                f"{_API_BASE}/images?action=initializeUpload",
                headers=self._headers,
                json={"initializeUploadRequest": {"owner": self._person_urn}},
                timeout=_TIMEOUT,
            )
            if init.status_code != 200:
                outcome = "failed" if 400 <= init.status_code < 500 else "uncertain"
                return None, f"LinkedIn image initialization HTTP {init.status_code} ({outcome})"

            value = (init.json() or {}).get("value") or {}
            upload_url = value.get("uploadUrl")
            image_urn = value.get("image")
            if not upload_url or not image_urn:
                return None, "LinkedIn image initialization returned no upload URL/image URN"

            upload = requests.put(
                upload_url,
                headers={"Content-Type": "application/octet-stream"},
                data=image_bytes,
                timeout=30,
            )
            if upload.status_code not in (200, 201, 202):
                outcome = "failed" if 400 <= upload.status_code < 500 else "uncertain"
                return None, f"LinkedIn image upload HTTP {upload.status_code} ({outcome})"

            # Images API is asynchronous. Poll the authoritative asset state.
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                status_resp = requests.get(
                    f"{_API_BASE}/images/{image_urn}",
                    headers=self._headers,
                    timeout=_TIMEOUT,
                )
                if status_resp.status_code == 200:
                    status = (status_resp.json() or {}).get("status")
                    if status == "AVAILABLE":
                        return image_urn, None
                    if status == "PROCESSING_FAILED":
                        return None, "LinkedIn image processing failed"
                elif status_resp.status_code == 404:
                    return None, "LinkedIn image asset not found after upload"
                elif status_resp.status_code >= 500:
                    return None, f"LinkedIn image status HTTP {status_resp.status_code} (uncertain)"
                time.sleep(0.75)

            return None, "LinkedIn image processing timed out (uncertain)"
        except Exception as exc:
            logger.error("LinkedIn image upload failed: %s", exc)
            return None, str(exc)

    # ── Publish ───────────────────────────────────────────────────────

    def publish(
        self,
        content: str,
        image_bytes: Optional[bytes] = None,
        image_url: Optional[str] = None,
        **kwargs: Any,
    ) -> ConnectorResult:
        if not self._token or not self._person_urn:
            return ConnectorResult(ok=False, platform=self.platform, error="Not configured", outcome="failed")

        image_urn = kwargs.get("image_urn")
        if image_url and not image_bytes and not image_urn:
            return ConnectorResult(
                ok=False, platform=self.platform,
                error="LinkedIn image_url requires media ingestion; provide image_bytes or a provider image_urn",
                outcome="failed",
            )

        if image_bytes and not image_urn:
            image_urn, upload_error = self._upload_image(image_bytes)
            if not image_urn:
                return ConnectorResult(
                    ok=False, platform=self.platform,
                    error=upload_error or "LinkedIn image upload failed",
                    outcome=("uncertain" if upload_error and "uncertain" in upload_error.lower() else "failed"),
                )

        try:
            payload = {
                "author": self._person_urn,
                "commentary": content[:3000],
                "visibility": "PUBLIC",
                "distribution": {
                    "feedDistribution": "MAIN_FEED",
                    "targetEntities": [],
                    "thirdPartyDistributionChannels": [],
                },
                **({"content": {"media": {"id": image_urn}}} if image_urn else {}),
                "lifecycleState": "PUBLISHED",
                "isReshareDisabledByAuthor": False,
            }

            resp = requests.post(
                f"{_API_BASE}/posts",
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
                error=f"HTTP {resp.status_code}", outcome=("failed" if 400 <= resp.status_code < 500 else "uncertain"),
            )
        except Exception as exc:
            logger.error("LinkedInConnector.publish: %s", exc)
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc))

    def lookup_post(self, post_id: str):
        if not self._token:
            return None
        try:
            resp = requests.get(f"{_API_BASE}/posts/{post_id}", headers=self._headers, timeout=_TIMEOUT)
            if resp.status_code == 200 and resp.json().get("id"):
                return True
            if resp.status_code == 404:
                return False
            return None
        except Exception:
            return None

    # ── Delete / Edit ─────────────────────────────────────────────────

    def delete(self, post_id: str) -> ConnectorResult:
        try:
            resp = requests.delete(
                f"{_API_BASE}/posts/{post_id}",
                headers=self._headers,
                timeout=_TIMEOUT,
            )
            return ConnectorResult(ok=resp.status_code in (200, 204), platform=self.platform, post_id=post_id)
        except Exception as exc:
            return ConnectorResult(ok=False, platform=self.platform, error=str(exc))

    def edit(self, post_id: str, content: str) -> ConnectorResult:
        # Published-post mutation is intentionally not part of this connector contract.
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
