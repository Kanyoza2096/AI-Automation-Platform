import asyncio
from collections import deque
from datetime import datetime, timezone, timedelta
import uuid
import logging
import hashlib
import hmac
from fastapi import Request
from api.dependencies import correlation_id_var

DRAFT_TTL_DAYS = 7

logger = logging.getLogger('kanyoza.api.stores')

# ═══════════════════════════════════════════════════════════════════════════
# ASYNC-SAFE IN-MEMORY STORES
# ═══════════════════════════════════════════════════════════════════════════


class DraftStore:
    """
    Redis-backed draft store with automatic TTL expiry.
    Falls back to in-memory dict if no Redis client is provided.
    Drafts survive Render sleep, restarts, and deployments.
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._fallback: dict[str, dict] = {}
        self._using_redis = redis_client is not None

    async def create(self, data: dict) -> dict:
        """Create a new draft and return it."""
        import json as _json
        draft_id = str(uuid.uuid4())
        draft = {
            "id": draft_id,
            **data,
            "state": "draft",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if self._using_redis:
            ttl_seconds = DRAFT_TTL_DAYS * 86400
            try:
                await self._redis.set(
                    f"draft:{draft_id}",
                    _json.dumps(draft),
                    ex=ttl_seconds,
                )
            except Exception:
                self._fallback[draft_id] = draft
        else:
            self._fallback[draft_id] = draft

        return draft

    async def get_all(self) -> list[dict]:
        """Return all active drafts."""
        import json as _json

        if self._using_redis:
            try:
                keys = await self._redis.keys("draft:*")
                drafts = []
                for key in keys:
                    raw = await self._redis.get(key)
                    if raw:
                        try:
                            drafts.append(_json.loads(raw))
                        except _json.JSONDecodeError:
                            pass
                return drafts
            except Exception:
                return list(self._fallback.values())

        return list(self._fallback.values())

    async def delete(self, draft_id: str) -> bool:
        """Delete a draft. Returns True if it existed."""
        if self._using_redis:
            try:
                existed = await self._redis.get(f"draft:{draft_id}")
                await self._redis.delete(f"draft:{draft_id}")
                return existed is not None
            except Exception:
                if draft_id in self._fallback:
                    del self._fallback[draft_id]
                    return True
                return False

        if draft_id in self._fallback:
            del self._fallback[draft_id]
            return True
        return False

    async def cleanup_expired(self):
        """Redis handles TTL expiry automatically. No-op for Redis backend."""
        if not self._using_redis:
            cutoff = datetime.now(timezone.utc) - timedelta(days=DRAFT_TTL_DAYS)
            expired = [
                k for k, v in self._fallback.items()
                if datetime.fromisoformat(v["created_at"]) < cutoff
            ]
            for k in expired:
                del self._fallback[k]


class AsyncAuditLog:
    """
    Thread-safe async audit log with Supabase persistence.
    A09: All mutations logged with correlation IDs.
    """

    def __init__(self, maxlen: int = 200):
        self._lock = asyncio.Lock()
        self._log: deque = deque(maxlen=maxlen)

    async def append(self, entry: dict):
        async with self._lock:
            self._log.appendleft(entry)

    async def get_recent(self, limit: int = 50) -> list[dict]:
        async with self._lock:
            return list(self._log)[:limit]

    async def seed_from_supabase(self):
        """A09: Seed in-memory log from persistent storage on startup."""
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                loop = asyncio.get_running_loop()
                res = await loop.run_in_executor(
                    None,
                    lambda: supabase_service.client.table("audit_logs")
                    .select("*")
                    .order("timestamp", desc=True)
                    .limit(50)
                    .execute(),
                )
                if res and res.data:
                    async with self._lock:
                        self._log.clear()
                        for entry in reversed(res.data):
                            self._log.appendleft(entry)
                    logger.info("Seeded %d audit log entries from Supabase.", len(res.data))
        except Exception as exc:
            logger.warning("Could not seed audit log from Supabase: %s", exc)


class AsyncWorkflowHistory:
    """
    Persistent workflow history — v2.0 (2026-10-31).

    Writes to the Supabase `workflow_history` table when a Supabase client is
    supplied; falls back to an in-memory deque when it is not. The in-memory
    deque is retained as a fallback but is NOT the source of truth in
    production; every append mirrors to Supabase, and every read queries
    Supabase first.

    Fixes:
      - Previous version kept history exclusively in a bounded in-memory deque
        (maxlen=20). Every process restart, deploy, or Render sleep wiped the
        history, so /api/v1/workflow/history returned [] even when workflows
        had run. This made the Workflow Runs page unable to show any data.
      - Added workspace_id filtering on reads. The previous version returned
        global history with workspace_id: null, which was confusing for
        workspace-scoped UIs.
      - Raised the in-memory maxlen from 20 to 200 so the fallback mode is
        still usable during transient Supabase outages.
      - find_and_update now also updates the Supabase row, not just the
        in-memory deque.
    """

    def __init__(self, maxlen: int = 200, supabase_client=None):
        self._lock = asyncio.Lock()
        self._history: deque = deque(maxlen=maxlen)
        self._sb = supabase_client

    async def append(self, entry: dict):
        async with self._lock:
            self._history.appendleft(entry)
        if self._sb is not None:
            try:
                await asyncio.to_thread(
                    lambda: self._sb.table("workflow_history").insert(entry).execute()
                )
            except Exception as exc:
                logger.warning("workflow_history insert failed: %s", exc)

    async def get_all(self) -> list[dict]:
        if self._sb is not None:
            try:
                res = await asyncio.to_thread(
                    lambda: self._sb.table("workflow_history")
                    .select("*")
                    .order("created_at", desc=True)
                    .limit(200)
                    .execute()
                )
                return list(res.data or [])
            except Exception as exc:
                logger.warning("workflow_history fetch failed: %s", exc)
        async with self._lock:
            return list(self._history)

    async def get_recent(self, limit: int = 20, workspace_id: str | None = None) -> list[dict]:
        if self._sb is not None:
            try:
                q = (
                    self._sb.table("workflow_history")
                    .select("*")
                    .order("created_at", desc=True)
                    .limit(limit)
                )
                if workspace_id:
                    q = q.eq("workspace_id", workspace_id)
                res = await asyncio.to_thread(lambda: q.execute())
                return list(res.data or [])
            except Exception as exc:
                logger.warning("workflow_history recent fetch failed: %s", exc)
        async with self._lock:
            items = list(self._history)[:limit]
        return items

    async def find_and_update(self, match_key: str, match_value: str, updates: dict) -> bool:
        if self._sb is not None:
            try:
                res = await asyncio.to_thread(
                    lambda: self._sb.table("workflow_history")
                    .update(updates)
                    .eq(match_key, match_value)
                    .execute()
                )
                if res.data:
                    return True
            except Exception as exc:
                logger.warning("workflow_history update failed: %s", exc)
        async with self._lock:
            for entry in self._history:
                if entry.get(match_key) == match_value:
                    entry.update(updates)
                    return True
        return False


class AsyncKeyStore:
    """Thread-safe API key store with hashed storage."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._keys: dict[str, dict] = {}

    async def add(self, key_id: str, entry: dict):
        async with self._lock:
            self._keys[key_id] = entry

    async def get_all_active(self) -> list[dict]:
        async with self._lock:
            return [
                {k: v for k, v in e.items() if k != "hashed"}
                for e in self._keys.values()
                if not e.get("revoked")
            ]

    async def revoke(self, key_id: str) -> bool:
        async with self._lock:
            if key_id in self._keys:
                self._keys[key_id]["revoked"] = True
                return True
        return False

    async def find_by_hash(self, hashed: str) -> dict | None:
        async with self._lock:
            for entry in self._keys.values():
                if entry.get("hashed") == hashed:
                    return entry
        return None


# ── Webhook Signature Verification — A02/A08 ──────────────────────────────


async def _verify_webhook_signature(request: Request) -> bool:
    """
    A02/A08: Constant-time HMAC-SHA256 verification for webhooks.
    Supports Facebook-style X-Hub-Signature-256 header and query secret fallback.
    """
    from config.settings import settings
    secret = getattr(settings, "webhook_secret", "") or ""
    if not secret:
        logger.error("A02: Webhook secret not configured — rejecting webhook")
        return False

    body = await request.body()

    # Facebook-style signature — proper HMAC-SHA256 (not simple hash concat)
    sig_header = request.headers.get("X-Hub-Signature-256", "")
    if sig_header.startswith("sha256="):
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig_header, expected):
            return True
        logger.warning("[%s] A08: Webhook rejected — invalid HMAC signature", correlation_id_var.get())
        return False

    logger.warning("[%s] A08: Webhook rejected — no valid signature provided", correlation_id_var.get())
    return False


# ── Social Account Helpers ────────────────────────────────────────────────

# Keys to strip recursively from social account API responses (spec §4.1/§4.4)
_SECRET_KEYS = frozenset({
    "access_token", "encrypted_credential", "page_access_token",
    "client_secret", "bot_token", "api_secret", "refresh_token",
    "password", "secret", "token", "private_key",
})
_NESTED_CONTAINERS = frozenset({"platform_config", "credentials", "config", "settings", "metadata"})


def _strip_credentials(data: dict) -> dict:
    """
    Spec §4.1/§4.4: Recursively remove all secret fields from the response dict.
    Nested containers (platform_config, credentials, config, settings, metadata)
    are also inspected. Returns a sanitized copy with credential_configured indicator.

    Rule: if any secret key existed → set has_credentials=True.
    """
    stripped = dict(data)
    had_credentials = False

    # Strip top-level secret keys
    for k in list(stripped.keys()):
        if k.lower() in _SECRET_KEYS:
            del stripped[k]
            had_credentials = True

    # Strip from nested containers
    for container_key in _NESTED_CONTAINERS:
        if container_key in stripped and isinstance(stripped[container_key], dict):
            nested = dict(stripped[container_key])
            for k in list(nested.keys()):
                if k.lower() in _SECRET_KEYS:
                    del nested[k]
                    had_credentials = True
            stripped[container_key] = nested

    stripped["has_credentials"] = had_credentials or data.get("has_credentials", False)
    stripped["credential_configured"] = stripped["has_credentials"]
    return stripped


def _get_account_token(account) -> str:
    token = getattr(account, "access_token", "") or ""
    if not token:
        pc = getattr(account, "platform_config", {}) or {}
        token = pc.get("page_access_token") or pc.get("access_token") or pc.get("bot_token") or ""
    return token


def _get_account_platform_id(account) -> str:
    pid = getattr(account, "platform_account_id", "") or ""
    if not pid:
        pc = getattr(account, "platform_config", {}) or {}
        pid = pc.get("page_id") or pc.get("phone_number_id") or pc.get("person_urn") or ""
    return pid
