"""
Webhook security — signature + replay/idempotency (architecture report §30).

Rules:
  - Signature verification required in production when secret is configured
  - Missing secret in production → reject (fail closed), not accept-all
  - Delivery IDs / message fingerprints tracked for replay protection
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger("kanyoza.core.webhook_security")


def _is_production() -> bool:
    env = (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "production").lower()
    return env in ("production", "prod", "staging")


def verify_hmac_sha256(
    body: bytes,
    signature_header: str,
    secret: str,
    *,
    prefix: str = "sha256=",
) -> bool:
    """Constant-time compare of HMAC-SHA256 signature."""
    if not secret or not body:
        return False
    if not signature_header or not signature_header.startswith(prefix):
        return False
    received = signature_header[len(prefix) :].strip()
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def verify_github_signature(body: bytes, signature_header: str) -> tuple[bool, str]:
    """
    Returns (ok, reason).
    Production without GITHUB_WEBHOOK_SECRET → fail closed.
    """
    secret = (os.getenv("GITHUB_WEBHOOK_SECRET") or "").strip()
    if not secret:
        if _is_production():
            return False, "github_webhook_secret_missing"
        # Dev only: allow with warning
        logger.warning("GITHUB_WEBHOOK_SECRET unset — accepting webhook in non-production")
        return True, "dev_unsigned_allowed"
    if not verify_hmac_sha256(body, signature_header or "", secret):
        return False, "invalid_signature"
    return True, "ok"


def verify_facebook_app_secret(
    body: bytes,
    signature_header: str,
    app_secret: Optional[str] = None,
) -> tuple[bool, str]:
    secret = (app_secret or os.getenv("APP_SECRET") or os.getenv("FACEBOOK_APP_SECRET") or "").strip()
    if not secret:
        if _is_production():
            return False, "facebook_app_secret_missing"
        logger.warning("APP_SECRET unset — accepting Facebook webhook in non-production")
        return True, "dev_unsigned_allowed"
    if not verify_hmac_sha256(body, signature_header or "", secret):
        return False, "invalid_signature"
    return True, "ok"


class WebhookIdempotencyStore:
    """Durable webhook claim/completion state with an in-process fallback.

    A delivery is marked *processing* when claimed and only becomes *completed*
    after the handler succeeds. Failed deliveries remain retryable after the
    durable TTL expires; this prevents the classic ``mark-before-process`` bug.
    """

    def __init__(self, ttl_seconds: int = 600, max_size: int = 20_000, db=None) -> None:
        if ttl_seconds < 1 or max_size < 1:
            raise ValueError("ttl_seconds and max_size must be positive")
        self._ttl = ttl_seconds
        self._max = max_size
        self._processing: dict[str, float] = {}
        self._completed: dict[str, float] = {}
        self._lock = threading.Lock()
        self._db = db

    def set_db(self, db) -> None:
        self._db = db

    def _get_db(self):
        if self._db is not None:
            return self._db
        try:
            from services.supabase import supabase_service
            return supabase_service.service_client or supabase_service.client
        except Exception:
            return None

    def _evict(self) -> None:
        now = time.monotonic()
        cutoff = now - self._ttl
        for store in (self._processing, self._completed):
            expired = [k for k, ts in store.items() if ts <= cutoff]
            for k in expired:
                del store[k]
        while len(self._processing) + len(self._completed) >= self._max:
            candidates = [(ts, k, store) for store in (self._processing, self._completed) for k, ts in store.items()]
            if not candidates:
                break
            _, key, store = min(candidates)
            del store[key]

    def claim(self, delivery_id: str) -> bool:
        """Atomically claim a delivery; return False when already active/completed."""
        if not delivery_id or not isinstance(delivery_id, str):
            return False
        key = delivery_id.strip()
        if not key:
            return False

        db = self._get_db()
        if db is not None:
            try:
                res = db.rpc(
                    "claim_webhook_delivery",
                    {"p_delivery_id": key, "p_ttl_seconds": self._ttl},
                ).execute()
                row = res.data[0] if isinstance(res.data, list) and res.data else res.data
                if isinstance(row, dict) and "claim_webhook_delivery" in row:
                    return bool(row["claim_webhook_delivery"])
                if isinstance(row, bool):
                    return row
                return bool(row)
            except Exception as exc:
                if _is_production():
                    logger.error("Durable webhook idempotency unavailable: %s", exc)
                    return False
                logger.debug("webhook durable idempotency unavailable: %s", exc)

        with self._lock:
            self._evict()
            if key in self._processing or key in self._completed:
                return False
            self._processing[key] = time.monotonic()
            return True

    def complete(self, delivery_id: str) -> None:
        """Mark a successfully processed delivery as completed."""
        if not delivery_id or not isinstance(delivery_id, str):
            return
        key = delivery_id.strip()
        if not key:
            return
        db = self._get_db()
        if db is not None:
            try:
                db.rpc("complete_webhook_delivery", {"p_delivery_id": key}).execute()
                return
            except Exception as exc:
                if _is_production():
                    logger.error("Durable webhook completion unavailable: %s", exc)
                    return
                logger.debug("webhook durable completion unavailable: %s", exc)
        with self._lock:
            self._processing.pop(key, None)
            self._completed[key] = time.monotonic()
            self._evict()

    def seen_or_mark(self, delivery_id: str) -> bool:
        """Compatibility API: claim a delivery without falsely marking it complete."""
        return not self.claim(delivery_id)


# Module singleton
webhook_idempotency = WebhookIdempotencyStore()
