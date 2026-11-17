"""Governed Website publishing connector.

The connector targets a configured KDO-compatible website publishing API. It is
intentionally not an arbitrary URL fetcher: the site base URL and API paths are
stored on the tenant's connected account, HTTPS is required, and mutation paths
are explicit.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional, TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import requests

from services.connectors.connector import ConnectorHealth, ConnectorResult, Platform

if TYPE_CHECKING:
    from models.social_account import SocialAccount

logger = logging.getLogger("kanyoza.connectors.website")
_TIMEOUT = 20


class WebsiteConnector:
    """Publish public content through a tenant-configured website API.

    Required ``platform_config`` fields:
      - ``base_url``: HTTPS origin, e.g. ``https://example.com/``
      - ``publish_path``: explicit API path, e.g. ``/api/kdo/content``

    Optional:
      - ``lookup_path_template``: path containing ``{post_id}``
      - ``delete_path_template`` / ``edit_path_template``
      - ``status``: provider publication status, default ``publish``
    The account ``access_token`` is sent as a Bearer credential.
    """

    platform = Platform.WEBSITE.value
    NATIVE_IDEMPOTENCY = True
    SAFE_RETRY_AFTER_AMBIGUOUS = False

    def __init__(self, social_account: Optional["SocialAccount"] = None) -> None:
        self._account = social_account
        config = dict(getattr(social_account, "platform_config", {}) or {})
        self._base_url = str(config.get("base_url") or "").strip()
        self._publish_path = str(config.get("publish_path") or "").strip()
        self._lookup_template = str(config.get("lookup_path_template") or "").strip()
        self._delete_template = str(config.get("delete_path_template") or "").strip()
        self._edit_template = str(config.get("edit_path_template") or "").strip()
        self._capabilities = {str(x).strip().upper() for x in (config.get("capabilities") or [])}
        # Fail closed: an account without explicit publish authority defaults to drafts.
        self._status = str(config.get("status") or (
            "publish" if "PUBLISH_ARTICLE" in self._capabilities else "draft"
        )).strip().lower()
        self._token = str(getattr(social_account, "access_token", "") or "").strip()

    def _validate_url(self, path: str) -> str:
        if not self._base_url or not self._publish_path:
            raise ValueError("Website connector is not configured")
        parsed = urlparse(self._base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Website connector requires an HTTPS base_url")
        if not path.startswith("/"):
            raise ValueError("Website connector API paths must start with '/'")
        return urljoin(self._base_url.rstrip("/") + "/", path.lstrip("/"))

    def _headers(self, idempotency_key: str = "") -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def publish(self, content: str, image_bytes: Optional[bytes] = None,
                image_url: Optional[str] = None, **kwargs: Any) -> ConnectorResult:
        del image_bytes
        try:
            url = self._validate_url(self._publish_path)
        except ValueError as exc:
            return ConnectorResult(False, self.platform, error=str(exc), outcome="failed")
        if not self._token:
            return ConnectorResult(False, self.platform, error="Website API credential is not configured", outcome="failed")
        requested_status = str(kwargs.get("status") or self._status).strip().lower()
        if requested_status == "publish" and "PUBLISH_ARTICLE" not in self._capabilities:
            return ConnectorResult(False, self.platform, error="PUBLISH_ARTICLE capability is not enabled", outcome="failed")
        if requested_status == "draft" and "CREATE_DRAFT" not in self._capabilities:
            return ConnectorResult(False, self.platform, error="CREATE_DRAFT capability is not enabled", outcome="failed")
        if requested_status not in {"publish", "draft"}:
            return ConnectorResult(False, self.platform, error="Unsupported website publication status", outcome="failed")
        title = str(kwargs.get("title") or kwargs.get("topic") or "KDO publication").strip()
        slug = str(kwargs.get("slug") or "").strip()
        idempotency_key = str(kwargs.get("idempotency_key") or "").strip()
        payload = {
            "title": title,
            "content": str(content or ""),
            "status": requested_status,
            "slug": slug or None,
            "image_url": image_url,
            "idempotency_key": idempotency_key,
            "workspace_id": str(getattr(self._account, "workspace_id", "") or ""),
        }
        try:
            response = requests.post(url, headers=self._headers(idempotency_key), json=payload, timeout=_TIMEOUT)
            if 200 <= response.status_code < 300:
                data = response.json() if response.content else {}
                object_id = str(data.get("id") or data.get("post_id") or data.get("content_id") or data.get("url") or "").strip()
                if not object_id:
                    return ConnectorResult(False, self.platform, error="Website accepted publication without a provider object ID", outcome="uncertain")
                return ConnectorResult(True, self.platform, post_id=object_id, metadata=data, outcome="succeeded")
            outcome = "failed" if 400 <= response.status_code < 500 else "uncertain"
            return ConnectorResult(False, self.platform, error=f"HTTP {response.status_code}", outcome=outcome)
        except requests.Timeout as exc:
            return ConnectorResult(False, self.platform, error=str(exc), outcome="uncertain")
        except requests.RequestException as exc:
            return ConnectorResult(False, self.platform, error=str(exc), outcome="uncertain")
        except Exception as exc:
            logger.exception("Website publication failed")
            return ConnectorResult(False, self.platform, error=str(exc), outcome="failed")

    def lookup_post(self, post_id: str) -> Any:
        if not self._lookup_template:
            return None
        path = self._lookup_template.replace("{post_id}", str(post_id))
        try:
            url = self._validate_url(path)
            response = requests.get(url, headers=self._headers(), timeout=_TIMEOUT)
            if response.status_code == 404:
                return False
            if not 200 <= response.status_code < 300:
                return None
            data = response.json() if response.content else {}
            return bool(data.get("id") or data.get("post_id") or data.get("content_id"))
        except Exception:
            return None

    def delete(self, post_id: str) -> ConnectorResult:
        if "DELETE_ARTICLE" not in self._capabilities:
            return ConnectorResult(False, self.platform, error="DELETE_ARTICLE capability is not enabled", outcome="failed")
        return self._mutate_template(self._delete_template, post_id, "delete")

    def edit(self, post_id: str, content: str) -> ConnectorResult:
        if "UPDATE_ARTICLE" not in self._capabilities:
            return ConnectorResult(False, self.platform, error="UPDATE_ARTICLE capability is not enabled", outcome="failed")
        return self._mutate_template(self._edit_template, post_id, "edit", {"content": content})

    def _mutate_template(self, template: str, post_id: str, operation: str, extra: Optional[dict[str, Any]] = None) -> ConnectorResult:
        if not template:
            return ConnectorResult(False, self.platform, error=f"Website provider does not configure {operation}", outcome="failed")
        try:
            url = self._validate_url(template.replace("{post_id}", str(post_id)))
            response = requests.delete(url, headers=self._headers()) if operation == "delete" else requests.patch(url, headers=self._headers(), json=extra or {}, timeout=_TIMEOUT)
            if 200 <= response.status_code < 300:
                return ConnectorResult(True, self.platform, post_id=str(post_id), outcome="succeeded")
            return ConnectorResult(False, self.platform, post_id=str(post_id), error=f"HTTP {response.status_code}", outcome=("failed" if response.status_code < 500 else "uncertain"))
        except Exception as exc:
            return ConnectorResult(False, self.platform, error=str(exc), outcome="uncertain")

    def health(self) -> ConnectorHealth:
        if not self._base_url or not self._publish_path or not self._token:
            return ConnectorHealth(False, self.platform, error="Not configured", outcome="failed")
        started = time.monotonic()
        try:
            url = self._validate_url(self._publish_path)
            response = requests.options(url, headers=self._headers(), timeout=10)
            return ConnectorHealth(200 <= response.status_code < 500, self.platform, round((time.monotonic() - started) * 1000, 1), outcome="succeeded" if response.status_code < 500 else "failed")
        except Exception as exc:
            return ConnectorHealth(False, self.platform, round((time.monotonic() - started) * 1000, 1), str(exc), outcome="uncertain")

    def validate(self) -> bool:
        return self.health().ok

    def fetch_comments(self, post_id: str, limit: int = 50):
        return []

    def fetch_messages(self, limit: int = 50):
        return []
