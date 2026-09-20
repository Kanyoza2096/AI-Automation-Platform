"""Governed public-website content connector.

The website connector treats a business website as a first-class publication
provider behind the same connector contract used by social channels.

It intentionally targets a small, explicit REST CMS contract rather than
assuming WordPress, a bespoke CMS, or a specific frontend stack.  A website
connection is configured on ``SocialAccount.platform_config``::

    {
      "base_url": "https://cms.example.com/api",
      "publish_path": "/content",
      "update_path": "/content/{post_id}",
      "lookup_path": "/content/{post_id}",
      "auth_mode": "bearer",
      "content_type": "news"
    }

Credentials are taken from ``access_token`` (bearer by default) or the
explicit ``api_key`` in platform_config.  The connector never treats an HTTP
success alone as a publication receipt: a concrete provider object ID is
required.  Transport ambiguity is reported as ``uncertain`` for
reconciliation.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, List, Optional, TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import requests

from services.connectors.connector import (
    ConnectorHealth,
    ConnectorResult,
    Comment,
    Message,
    Platform,
)

if TYPE_CHECKING:
    from models.social_account import SocialAccount

logger = logging.getLogger("kanyoza.connectors.website")

_TIMEOUT = 20
_MAX_CONTENT = 500_000
_SAFE_PATH_RE = re.compile(r"^/[^\s]*$")


class WebsiteConnector:
    """REST website/CMS connector for public business content.

    Supported mutations:
      * ``publish`` — create a news/blog/page item.
      * ``edit`` — update an existing item.

    The provider API must return a stable object identifier in one of
    ``id``, ``post_id``, ``content_id``, ``article_id``, ``data.id`` or
    ``data.post_id`` for a successful mutation.
    """

    platform = Platform.WEBSITE.value
    NATIVE_IDEMPOTENCY = False
    SAFE_RETRY_AFTER_AMBIGUOUS = False

    def __init__(self, social_account: Optional["SocialAccount"] = None) -> None:
        self._account = social_account
        config = dict(getattr(social_account, "platform_config", {}) or {}) if social_account else {}
        self._config = config
        self._base_url = str(config.get("base_url") or "").strip().rstrip("/")
        self._token = str(getattr(social_account, "access_token", "") or "").strip() if social_account else ""
        self._api_key = str(config.get("api_key") or "").strip()
        self._auth_mode = str(config.get("auth_mode") or "bearer").strip().lower()
        self._content_type = str(config.get("content_type") or "article").strip().lower()
        self._publish_path = str(config.get("publish_path") or "/content").strip()
        self._update_path = str(config.get("update_path") or "/content/{post_id}").strip()
        self._lookup_path = str(config.get("lookup_path") or self._update_path).strip()
        self._health_path = str(config.get("health_path") or self._lookup_path).strip()
        self._allow_insecure_http = bool(config.get("allow_insecure_http", False))
        self._validate_config()

    def _validate_config(self) -> None:
        if not self._base_url:
            return
        parsed = urlparse(self._base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("website connector base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("website connector base_url must not contain credentials")
        if parsed.scheme == "http" and not self._allow_insecure_http:
            raise ValueError("website connector requires HTTPS unless allow_insecure_http is explicitly enabled")
        for name, path in (("publish_path", self._publish_path), ("update_path", self._update_path),
                           ("lookup_path", self._lookup_path), ("health_path", self._health_path)):
            if not _SAFE_PATH_RE.match(path):
                raise ValueError(f"website connector {name} must be an absolute API path")

    @property
    def _account_id(self) -> Optional[str]:
        return getattr(self._account, "id", None) if self._account else None

    def _url(self, path: str, **values: Any) -> str:
        formatted = path.format(**values)
        # urljoin with an absolute path keeps the configured host while
        # allowing deployments to place the CMS behind a path prefix.
        return urljoin(self._base_url.rstrip("/") + "/", formatted.lstrip("/"))

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._auth_mode == "bearer":
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
        elif self._auth_mode == "api_key":
            if self._api_key:
                headers[str(self._config.get("api_key_header") or "X-API-Key")] = self._api_key
        elif self._auth_mode == "none":
            pass
        else:
            raise ValueError("website connector auth_mode must be bearer, api_key, or none")
        return headers

    @staticmethod
    def _provider_id(data: Any) -> Optional[str]:
        if not isinstance(data, dict):
            return None
        for key in ("id", "post_id", "content_id", "article_id", "page_id", "object_id"):
            value = data.get(key)
            if value:
                return str(value)
        nested = data.get("data")
        if isinstance(nested, dict):
            return WebsiteConnector._provider_id(nested)
        result = data.get("result")
        if isinstance(result, dict):
            return WebsiteConnector._provider_id(result)
        return None

    @staticmethod
    def _error_text(response: requests.Response) -> str:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                return str(payload.get("error") or payload.get("message") or payload.get("detail") or f"HTTP {response.status_code}")[:500]
        except Exception:
            pass
        return (response.text or f"HTTP {response.status_code}")[:500]

    def _receipt(self, *, ok: bool, post_id: Optional[str] = None, error: Optional[str] = None,
                 outcome: str = "unknown", metadata: Optional[dict[str, Any]] = None) -> ConnectorResult:
        return ConnectorResult(
            ok=bool(ok), platform=self.platform, post_id=post_id, error=error,
            metadata={
                "website_account_id": self._account_id,
                "content_type": self._content_type,
                **(metadata or {}),
            }, outcome=outcome,
        )

    def _ensure_ready(self) -> Optional[ConnectorResult]:
        if not self._base_url:
            return self._receipt(ok=False, error="Website connector is not configured: base_url is required", outcome="failed")
        if self._auth_mode == "bearer" and not self._token:
            return self._receipt(ok=False, error="Website connector is not configured: access_token is required", outcome="failed")
        if self._auth_mode == "api_key" and not self._api_key:
            return self._receipt(ok=False, error="Website connector is not configured: api_key is required", outcome="failed")
        return None

    def publish(self, content: str, image_bytes: Optional[bytes] = None,
                image_url: Optional[str] = None, **kwargs: Any) -> ConnectorResult:
        ready = self._ensure_ready()
        if ready:
            return ready
        content = str(content or "")
        if not content.strip():
            return self._receipt(ok=False, error="website publish requires content", outcome="failed")
        if len(content) > _MAX_CONTENT:
            return self._receipt(ok=False, error=f"website content exceeds {_MAX_CONTENT} characters", outcome="failed")
        if image_bytes and not image_url:
            return self._receipt(ok=False, error="website connector does not silently upload raw media; provide image_url or a CMS-specific media connector", outcome="failed")

        payload = self._payload(content, image_url=image_url, **kwargs)
        try:
            response = requests.post(self._url(self._publish_path), headers=self._headers(), json=payload, timeout=_TIMEOUT)
            if 200 <= response.status_code < 300:
                data = response.json() if response.content else {}
                provider_id = self._provider_id(data)
                if provider_id:
                    return self._receipt(ok=True, post_id=provider_id, outcome="succeeded", metadata={"http_status": response.status_code})
                return self._receipt(ok=False, error="Website provider returned success without a content ID", outcome="uncertain", metadata={"http_status": response.status_code, "provider_response": data})
            outcome = "failed" if 400 <= response.status_code < 500 else "uncertain"
            return self._receipt(ok=False, error=self._error_text(response), outcome=outcome, metadata={"http_status": response.status_code})
        except requests.Timeout as exc:
            return self._receipt(ok=False, error=str(exc), outcome="uncertain", metadata={"exception_type": type(exc).__name__})
        except requests.RequestException as exc:
            return self._receipt(ok=False, error=str(exc), outcome="uncertain", metadata={"exception_type": type(exc).__name__})
        except Exception as exc:
            logger.exception("WebsiteConnector.publish failed")
            return self._receipt(ok=False, error=str(exc), outcome="failed", metadata={"exception_type": type(exc).__name__})

    def _payload(self, content: str, **kwargs: Any) -> dict[str, Any]:
        payload = {
            "content_type": str(kwargs.get("content_type") or self._content_type),
            "title": str(kwargs.get("title") or "").strip(),
            "slug": str(kwargs.get("slug") or "").strip(),
            "excerpt": str(kwargs.get("excerpt") or "").strip(),
            "content": content,
            "status": str(kwargs.get("status") or "publish").strip().lower(),
        }
        for key in ("published_at", "author", "category", "tags", "featured_image_url", "canonical_url", "metadata"):
            if kwargs.get(key) is not None:
                payload[key] = kwargs[key]
        if kwargs.get("image_url"):
            payload["featured_image_url"] = kwargs["image_url"]
        return payload

    def lookup_post(self, post_id: str) -> Any:
        ready = self._ensure_ready()
        if ready:
            return None
        if not str(post_id or "").strip():
            return None
        try:
            response = requests.get(self._url(self._lookup_path, post_id=str(post_id)), headers=self._headers(), timeout=_TIMEOUT)
            if response.status_code == 404:
                return False
            if not 200 <= response.status_code < 300:
                return None
            data = response.json() if response.content else {}
            provider_id = self._provider_id(data)
            return bool(provider_id) if provider_id else None
        except Exception:
            return None

    def delete(self, post_id: str) -> ConnectorResult:
        return self._receipt(ok=False, error="Website connector intentionally does not expose delete; use a separately governed content-retirement action", outcome="failed")

    def edit(self, post_id: str, content: str = "", **kwargs: Any) -> ConnectorResult:
        ready = self._ensure_ready()
        if ready:
            return ready
        post_id = str(post_id or "").strip()
        if not post_id:
            return self._receipt(ok=False, error="website update requires post_id", outcome="failed")
        if not str(content or "").strip():
            return self._receipt(ok=False, error="website update requires content", outcome="failed")
        payload = self._payload(str(content), **kwargs)
        method = str(self._config.get("update_method") or "PATCH").upper()
        if method not in {"PATCH", "PUT"}:
            return self._receipt(ok=False, error="website update_method must be PATCH or PUT", outcome="failed")
        try:
            response = requests.request(method, self._url(self._update_path, post_id=post_id), headers=self._headers(), json=payload, timeout=_TIMEOUT)
            if 200 <= response.status_code < 300:
                data = response.json() if response.content else {}
                provider_id = self._provider_id(data) or post_id
                return self._receipt(ok=True, post_id=provider_id, outcome="succeeded", metadata={"http_status": response.status_code})
            outcome = "failed" if 400 <= response.status_code < 500 else "uncertain"
            return self._receipt(ok=False, post_id=post_id, error=self._error_text(response), outcome=outcome, metadata={"http_status": response.status_code})
        except (requests.Timeout, requests.RequestException) as exc:
            return self._receipt(ok=False, post_id=post_id, error=str(exc), outcome="uncertain", metadata={"exception_type": type(exc).__name__})
        except Exception as exc:
            logger.exception("WebsiteConnector.edit failed")
            return self._receipt(ok=False, post_id=post_id, error=str(exc), outcome="failed", metadata={"exception_type": type(exc).__name__})

    def health(self) -> ConnectorHealth:
        ready = self._ensure_ready()
        if ready:
            return ConnectorHealth(ok=False, platform=self.platform, error=ready.error, outcome="failed")
        t0 = time.monotonic()
        try:
            response = requests.get(self._url(self._health_path), headers=self._headers(), timeout=10)
            ms = round((time.monotonic() - t0) * 1000, 1)
            if 200 <= response.status_code < 300:
                return ConnectorHealth(ok=True, platform=self.platform, latency_ms=ms, outcome="succeeded", metadata={"http_status": response.status_code})
            return ConnectorHealth(ok=False, platform=self.platform, latency_ms=ms, error=self._error_text(response), outcome=("failed" if 400 <= response.status_code < 500 else "uncertain"), metadata={"http_status": response.status_code})
        except (requests.Timeout, requests.RequestException) as exc:
            return ConnectorHealth(ok=False, platform=self.platform, latency_ms=round((time.monotonic() - t0) * 1000, 1), error=str(exc), outcome="uncertain")

    def validate(self) -> bool:
        return self.health().ok

    def fetch_comments(self, post_id: str, limit: int = 50) -> List[Comment]:
        return []

    def fetch_messages(self, limit: int = 50) -> List[Message]:
        return []
