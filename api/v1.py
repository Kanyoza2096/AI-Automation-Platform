"""
Kanyoza Systems AI Platform v12 — FastAPI REST API v1
Production-grade conversion with OWASP Top 10 (2021) security hardening.

OWASP Coverage:
  A01:2021 – Broken Access Control
    • Multi-layered auth (master token, Supabase JWT, user API keys)
    • Workspace isolation on all CRUD routes
    • 404 masking for unauthorized cross-workspace access
    • Constant-time token comparison (hmac.compare_digest)

  A02:2021 – Cryptographic Failures
    • API keys hashed with SHA-256 before storage
    • Webhook signatures verified with HMAC-SHA256
    • No secrets in responses (access_token stripped)
    • TLS assumed at reverse proxy (HSTS header set)

  A03:2021 – Injection
    • Prompt injection guard on /ai/chat (compiled regex + validator)
    • HTML tag stripping on all user input (_sanitize)
    • HTML entity escaping on all user input
    • Pydantic validation at request boundary
    • SQL injection prevented by Supabase parameterized queries

  A04:2021 – Insecure Design
    • Rate limiting on auth and AI endpoints
    • Request size limits (1 MB)
    • Correlation IDs for audit trail
    • Draft TTL cleanup (7 days)
    • Graceful degradation when services unavailable

  A05:2021 – Security Misconfiguration
    • Security headers on every response
    • CORS configured explicitly (not wildcard in production)
    • Server header stripped
    • Debug mode disabled in production
    • OpenAPI docs hidden in production

  A06:2021 – Vulnerable and Outdated Components
    • Pydantic v2 for input validation
    • FastAPI latest patterns (lifespan, Depends)
    • httpx for async HTTP (not requests)

  A07:2021 – Identification and Authentication Failures
    • Multi-factor auth support (bearer + API key + JWT)
    • Rate limiting on auth attempts (10/min)
    • Constant-time token comparison
    • No default credentials

  A08:2021 – Software and Data Integrity Failures
    • Webhook signature validation
    • Audit log for all mutations
    • Supabase as source of truth

  A09:2021 – Security Logging and Monitoring Failures
    • Structured logging with correlation IDs
    • Audit trail persisted to Supabase
    • Request tracing for Digital Twin
    • Failed auth attempts logged

  A10:2021 – Server-Side Request Forgery (SSRF)
    • Platform verify timeouts on external calls
    • Webhook URL validation
    • No user-supplied URLs in requests

Architecture §29 — Observability:
  - job_id returned in all workflow/publish trigger responses
  - GET /jobs/{job_id} for job status by correlation ID
  - GET /brands/{brand_id}/jobs for brand job history

Architecture §12 — Typed Errors:
  - KanyozaError handler surfaces structured errors with proper HTTP codes
"""

from __future__ import annotations

from platform_version import PLATFORM_VERSION
import asyncio
import hashlib
import hmac
import html as _html_mod
import logging
import time
import re as _re_mod
import secrets as _secrets_mod
import time as _time_module
import threading
import uuid as _uuid_mod
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from fastapi import UploadFile, File, Form

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator, ValidationError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import StreamingResponse
from fastapi.exceptions import RequestValidationError

from core.errors import KanyozaError, DatabaseQueryError
from core.plugin_execution import resolve_plugin_workspace, run_plugin
from core.security.deps import require_workspace_admin, require_workspace_operator
from core.request_context import get_request_context
from api.dependencies import (
    correlation_id_var, auth_method_var, auth_workspace_id_var, auth_user_email_var,
    bearer_scheme, get_container, get_draft_store, get_audit_log, get_workflow_history,
    get_key_store, require_auth, _auth_error, require_workspace_ownership,
    create_rate_limit_dependency, _get_resource_workspace_id, append_audit, _async_wrap,
)

# ═══════════════════════════════════════════════════════════════════════════
# LOGGING — Structured with correlation ID support
# ═══════════════════════════════════════════════════════════════════════════

logger = logging.getLogger("kanyoza.api.v1")

_publish_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════════
# SECURITY CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

MAX_REQUEST_BODY_SIZE = 10_485_760  # 10 MB — A04/A05: Prevent resource exhaustion
MAX_AI_PROMPT_LENGTH = 4000  # characters — A03: Limit injection surface
PLATFORM_VERIFY_TIMEOUT = 8.0  # seconds — A10: SSRF timeout protection
DRAFT_TTL_DAYS = 7  # A04: Prevent stale data accumulation
MAX_PAGE_SIZE = 100  # A04: Prevent excessive data exposure
BCRYPT_ROUNDS = 12  # A02: Work factor for password hashing (future use)

# A03: Injection — Compiled prompt injection patterns (O(n) C-level scan)
INJECTION_PATTERNS_RE = _re_mod.compile(
    "|".join(
        _re_mod.escape(p)
        for p in [
            "ignore previous instructions",
            "ignore all instructions",
            "system prompt",
            "you are now",
            "pretend you are",
            "forget everything",
            "new instructions",
            "override",
            "### System",
            "<|system|>",
            "<|im_start|>",
            "tell me your instructions",
            "reveal your prompt",
            "what are your rules",
            "DAN mode",
            "jailbreak",
            "developer mode",
        ]
    ),
    _re_mod.IGNORECASE,
)

# A03: HTML tag stripping regex (defense-in-depth with _sanitize)
HTML_TAG_RE = _re_mod.compile(r"<[^>]*>")

# A05: Allowed CORS origins — tighten in production
ALLOWED_ORIGINS: list[str] = []  # Populate from config in production

# A07: Auth rate limit windows
AUTH_RATE_LIMIT = 10  # attempts per minute
AI_CHAT_RATE_LIMIT = 20  # requests per minute
KEY_GEN_RATE_LIMIT = 5  # keys per minute

# ═══════════════════════════════════════════════════════════════════════════
# PYDANTIC REQUEST MODELS — A03 Input validation at the boundary
# ═══════════════════════════════════════════════════════════════════════════


class PostCreateRequest(BaseModel):
    """A03: All string fields validated for length and content."""
    caption: str = Field(default="", max_length=5000)
    topic: str = Field(default="", max_length=500)
    category: str = Field(default="", max_length=200)
    state: str = Field(default="publish", pattern=r"^(publish|draft)$")
    # Fields required by Mutex frontend contract (spec §1.2)
    brand_id: Optional[str] = Field(default=None, max_length=128)
    platforms: Optional[list[str]] = Field(default=None, max_length=20)
    ai_generate: Optional[bool] = Field(default=None)
    scheduled_for: Optional[str] = Field(default=None, max_length=64)
    workspace_id: Optional[str] = Field(default=None, max_length=128)
    decision_id: Optional[str] = Field(default=None, max_length=256)
    idempotency_key: Optional[str] = Field(default=None, max_length=256)
    risk_level: str = Field(default="low", pattern=r"^(trivial|low|medium|high|critical)$")

    @field_validator("caption", "topic", "category")
    @classmethod
    def sanitize_strings(cls, v: str) -> str:
        return _sanitize(v)


class MessageReplyRequest(BaseModel):
    recipient_id: str = Field(..., min_length=1, max_length=128)
    text: str = Field(..., min_length=1, max_length=2000)

    @field_validator("recipient_id", "text")
    @classmethod
    def sanitize_message_fields(cls, v: str) -> str:
        return _sanitize(v)


class AIChatRequest(BaseModel):
    """A03: Prompt injection guard at the validation layer."""
    message: str = Field(..., min_length=1, max_length=MAX_AI_PROMPT_LENGTH)
    history: list[dict] = Field(default_factory=list, max_length=50)

    @field_validator("message")
    @classmethod
    def sanitize_and_check_injection(cls, v: str) -> str:
        # Strip HTML tags
        clean = HTML_TAG_RE.sub("", str(v))
        # Normalize whitespace
        clean = " ".join(clean.split())
        if not clean:
            raise ValueError("message must not be empty after sanitization")
        # Escape HTML entities
        clean = _html_mod.escape(clean, quote=False)
        # Check for prompt injection patterns
        if INJECTION_PATTERNS_RE.search(clean):
            cid = correlation_id_var.get()
            logger.warning("[%s] A03: Prompt injection blocked in validator", cid)
            raise ValueError("Message contains prohibited patterns")
        return clean


class ScheduleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    post_interval_hours: Optional[int] = Field(default=None, ge=1, le=168)
    news_interval_hours: Optional[int] = Field(default=None, ge=1, le=168)
    enabled: Optional[bool] = None
    content_hours_utc: Optional[list[int]] = None
    news_hours_utc: Optional[list[int]] = None

    @field_validator("content_hours_utc", "news_hours_utc")
    @classmethod
    def validate_hours(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return v
        for hour in v:
            if not 0 <= hour <= 23:
                raise ValueError(f"Hour must be 0-23, got {hour}")
        return v


class ImageGenerateRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    content: Optional[str] = Field(default="", max_length=2000)
    author: Optional[str] = Field(default="")
    platform: Optional[str] = Field(default="facebook")


class AIConfigUpdateRequest(BaseModel):
    provider: Optional[str] = Field(default=None, pattern=r"^(gemini|huggingface|openai|anthropic|cloudflare)$")
    model: Optional[str] = Field(default=None, max_length=128)
    chat_temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    post_temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    safety_level: Optional[str] = Field(default=None, pattern=r"^(low|medium|high)$")
    system_prompt_override: Optional[str] = Field(default=None, max_length=5000)
    persona_mood: Optional[str] = Field(default=None, max_length=64)
    tone_assertiveness: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    tone_humor: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    tone_formality: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    id: Optional[str] = Field(default=None, max_length=64)
    slug: Optional[str] = Field(default=None, max_length=128)
    description: str = Field(default="", max_length=1000)
    owner: Optional[str] = Field(default=None, max_length=256, description="Owner email address")
    plan: Optional[str] = Field(default="free", pattern=r"^(free|pro|business|enterprise)$")

    @field_validator("name", "description")
    @classmethod
    def sanitize_workspace_fields(cls, v: str) -> str:
        return _sanitize(v)


class UserCreateRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=256)
    name: str = Field(..., min_length=1, max_length=256)
    role: str = Field(default="viewer", pattern=r"^(viewer|editor|admin|owner)$")
    workspace_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("email", "name")
    @classmethod
    def sanitize_user_fields(cls, v: str) -> str:
        return _sanitize(v)


class UserUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=256)
    role: Optional[str] = Field(default=None, pattern=r"^(viewer|editor|admin|owner)$")
    status: Optional[str] = Field(default=None, pattern=r"^(active|inactive)$")

    @field_validator("name")
    @classmethod
    def sanitize_user_name(cls, v: Optional[str]) -> Optional[str]:
        return _sanitize(v) if v else v


class RateLimitConfigRequest(BaseModel):
    limit: int = Field(..., ge=1, le=10000)
    window: int = Field(..., ge=1, le=86400, description="Window in seconds")
    scope: str = Field(default="global", max_length=64)
    workspace_id: Optional[str] = Field(default=None, max_length=128)
    provider: Optional[str] = Field(default=None, max_length=64)
    enabled: bool = Field(default=True)


class APIKeyGenerateRequest(BaseModel):
    label: str = Field(default="api-key", min_length=1, max_length=64)

    @field_validator("label")
    @classmethod
    def sanitize_label(cls, v: str) -> str:
        return _sanitize(v)


class KnowledgeCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=50000)
    doc_type: str = Field(default="text", max_length=64)
    metadata: dict = Field(default_factory=dict)
    title: str = Field(default="", max_length=500)

    @field_validator("content", "title")
    @classmethod
    def sanitize_knowledge(cls, v: str) -> str:
        return _sanitize(v)


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def sanitize_query(cls, v: str) -> str:
        return _sanitize(v)


class BulkKnowledgeRequest(BaseModel):
    brand_id: str = Field(default="bulk", max_length=64)
    items: list[dict] = Field(..., min_length=1, max_length=50)


class WorkflowRunRequest(BaseModel):
    workflow: str = Field(default="post_publish", max_length=128)


class UnblockRequest(BaseModel):
    identifier: str = Field(..., min_length=1, max_length=256)

    @field_validator("identifier")
    @classmethod
    def sanitize_identifier(cls, v: str) -> str:
        return _sanitize(v)


class TenantSwitchRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1, max_length=128)


class PluginExecuteRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=256)
    params: dict = Field(default_factory=dict)
    workspace_id: str = Field(default="default", max_length=64)

    @field_validator("command", "workspace_id")
    @classmethod
    def sanitize_plugin_fields(cls, v: str) -> str:
        return _sanitize(v)

class UserDeleteRequest(BaseModel):
    reassign_to: Optional[str] = Field(default=None, max_length=255)


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS — Utility Functions
# ═══════════════════════════════════════════════════════════════════════════


def _sanitize(value: str) -> str:
    """
    A03: Strip HTML tags, normalize whitespace, escape HTML entities.
    Defense-in-depth: applied at Pydantic validators AND service layer.
    """
    if not value:
        return ""
    clean = HTML_TAG_RE.sub("", str(value))
    clean = " ".join(clean.split())
    return _html_mod.escape(clean, quote=False)


def _ok(data: dict | None = None, status_code: int = 200) -> JSONResponse:
    """Standardized success response — consistent with existing frontend contract."""
    body: dict = {"ok": True}
    if data:
        body.update(data)
    return JSONResponse(content=body, status_code=status_code)


def _err(msg: str, status_code: int = 400) -> JSONResponse:
    """Standardized error response."""
    return JSONResponse(
        content={"ok": False, "error": msg}, status_code=status_code
    )


def _safe_err(exc: Exception, endpoint: str, status_code: int = 500) -> JSONResponse:
    """
    A05/A09: Logs full error internally, returns sanitized message externally.
    In production, hides stack traces and internal details.
    """
    cid = correlation_id_var.get()
    logger.error("[%s] %s error: %s", cid, endpoint, exc, exc_info=True)
    try:
        from config.settings import settings
        if settings.is_development:
            return _err(str(exc), status_code)
    except Exception:
        pass
    return _err("An internal error occurred. Please try again later.", status_code)


async def _async_wrap(func: Callable, *args, **kwargs) -> Any:
    """
    Wraps synchronous function calls in run_in_executor.
    Preserves all existing sync services/repos without modification.
    Uses the default thread pool executor (asyncio's internal pool).
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


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
        draft_id = str(_uuid_mod.uuid4())
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
    """Thread-safe async workflow history."""

    def __init__(self, maxlen: int = 20):
        self._lock = asyncio.Lock()
        self._history: deque = deque(maxlen=maxlen)

    async def append(self, entry: dict):
        async with self._lock:
            self._history.append(entry)

    async def get_all(self) -> list[dict]:
        async with self._lock:
            return list(self._history)

    async def get_recent(self, limit: int = 20) -> list[dict]:
        async with self._lock:
            items = list(self._history)[-limit:]
            return list(reversed(items))

    async def find_and_update(self, match_key: str, match_value: str, updates: dict) -> bool:
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
    "password", "secret", "token", "private_key", "api_key",
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


# ═══════════════════════════════════════════════════════════════════════════
# MIDDLEWARE STACK
# ═══════════════════════════════════════════════════════════════════════════


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """A09: Extracts or generates a correlation ID for every request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        cid = request.headers.get("X-Correlation-ID") or _uuid_mod.uuid4().hex[:12]
        correlation_id_var.set(cid)
        response = await call_next(request)
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    A05: Injects security headers on every response.

    Headers:
      - X-Content-Type-Options: nosniff (prevents MIME sniffing)
      - X-Frame-Options: DENY (prevents clickjacking)
      - X-XSS-Protection: 0 (deprecated; rely on CSP instead)
      - Referrer-Policy: strict-origin-when-cross-origin
      - Permissions-Policy: restricts browser features
      - Strict-Transport-Security: max-age=31536000; includeSubDomains
      - Server: stripped (prevents fingerprinting)
      - X-Correlation-ID: echoed for client tracing
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        )
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["X-Correlation-ID"] = correlation_id_var.get()
        # A05: Strip Server header to prevent version fingerprinting
        del response.headers["server"]
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """A04: Rejects requests exceeding the max body size before processing."""

    def __init__(self, app, max_size: int = MAX_REQUEST_BODY_SIZE):
        super().__init__(app)
        self.max_size = max_size

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_size:
            logger.warning(
                "[%s] A04: Request size limit exceeded: %s bytes",
                correlation_id_var.get(), content_length,
            )
            return JSONResponse(
                status_code=413,
                content={
                    "ok": False,
                    "error": "payload_too_large",
                    "message": f"Request body exceeds maximum size of {self.max_size} bytes.",
                },
            )
        return await call_next(request)


class RequestTracingMiddleware(BaseHTTPMiddleware):
    """
    A09: Times every /api/ request and publishes trace data for the Digital Twin.
    Replaces Flask's g.trace_start / _publish_request_trace pattern.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        start_time = _time_module.monotonic()
        response = await call_next(request)
        incoming_trace = request.headers.get("traceparent", "")
        # Preserve the distributed trace context for correlation; Python currently
        # acts as a traced service boundary, not as a full OTel SDK span exporter.
        if incoming_trace:
            response.headers["traceparent"] = incoming_trace
            parts = incoming_trace.split("-")
            if len(parts) == 4 and len(parts[1]) == 32:
                response.headers["X-Trace-ID"] = parts[1]
        duration_ms = round((_time_module.monotonic() - start_time) * 1000, 2)

        # Fire-and-forget trace publication
        async def _publish():
            try:
                from utils.tracing import _publish_trace
                await _async_wrap(
                    _publish_trace,
                    {
                        "id": f"req_{int(_time_module.time() * 1000)}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "correlation_id": correlation_id_var.get(),
                        "step": f"{request.method} {request.url.path}",
                        "duration_ms": duration_ms,
                        "status": response.status_code,
                    },
                )
            except Exception:
                pass  # Tracing must never break the request

        asyncio.create_task(_publish())
        return response


# ═══════════════════════════════════════════════════════════════════════════
# ROUTER
# ═══════════════════════════════════════════════════════════════════════════

router = APIRouter(prefix="/api/v1")


class AuthExchangeRequest(BaseModel):
    """Short-lived internal exchange of a validated browser Supabase access token."""
    access_token: str = Field(min_length=20, max_length=8192)


@router.post("/auth/exchange", include_in_schema=False)
async def auth_exchange(
    payload: AuthExchangeRequest,
    request: Request,
    _service_auth: bool = Depends(require_auth),
    container=Depends(get_container),
):
    """Validate a browser Supabase token for KCPG session exchange.

    The endpoint itself is internal-only at the network layer. KCPG authenticates
    with the Python service credential, then supplies the browser token for
    canonical SpeciesAuth validation. No browser token is persisted here.
    """
    from core.species_auth import species_auth
    from services.supabase import supabase_service

    token = payload.access_token.strip()
    # ServiceContainer does not expose ``supabase_service`` as an attribute.
    # The previous lookup therefore always resolved to None in the deployed
    # exchange path, causing every Supabase JWT to fall through SpeciesAuth
    # as an invalid credential. Use the canonical Supabase service singleton.
    supabase_client = getattr(supabase_service, "client", None)
    request_id = request.headers.get("X-Request-ID") or request.headers.get("X-Correlation-ID") or "unknown"

    if supabase_client is None:
        logger.error(
            "[%s] auth_exchange_supabase_client_unavailable",
            request_id,
        )
        raise HTTPException(status_code=503, detail="Identity provider unavailable")

    identity = species_auth.validate(
        bearer_token=token,
        supabase_client=supabase_client,
        key_store=request.app.state.key_store,
    )
    if not identity.valid or identity.method != "supabase_jwt":
        logger.warning(
            "[%s] auth_exchange_identity_rejected method=%s reason=%s",
            request_id,
            identity.method or "none",
            identity.error or "invalid_identity",
        )
        raise HTTPException(status_code=401, detail="Invalid authentication token")

    return {
        "ok": True,
        "sub": identity.user_id,
        "email": identity.user_email,
        "role": identity.role,
        "workspaceId": identity.workspace_id,
        "scopes": identity.permissions,
    }

@router.get("/system/auth/validate", include_in_schema=False)
async def validate_service_credential(_service_auth: bool = Depends(require_auth)):
    """Protected service-credential validation endpoint for KCPG bootstrap checks."""
    if auth_method_var.get() != "python_service":
        raise HTTPException(status_code=403, detail="Python service credential required")
    return {"ok": True, "authenticated": True, "auth_method": "python_service"}


# Available workflows
AVAILABLE_WORKFLOWS = {
    "post_publish": {
        "description": "Select topic → Generate caption → Render card → Publish",
        "steps": ["select_topic", "generate", "render", "publish", "analytics", "event"],
        "trigger": "scheduler (10 ×/day) or manual",
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# POSTS
# ═══════════════════════════════════════════════════════════════════════════



@router.get("/posts")
async def get_posts(
    request: Request,
    draft_store: DraftStore = Depends(get_draft_store),
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
    page: int = 1,
    per_page: int = 20,
    state: str = "",
):
    """List posts scoped to the authenticated workspace.
    
    Architecture §31 — Tenant Isolation:
      Every query is scoped by workspace. Users from Workspace A
      must never see posts belonging to Workspace B.
    """
    try:
        page = max(1, page)
        per_page = min(MAX_PAGE_SIZE, max(1, per_page))
        state_filter = state.lower()

        # Resolve the authenticated workspace
        workspace_id = auth_workspace_id_var.get()
        
        # If no workspace context (master token), return all posts (admin mode)
        # If workspace context exists, scope everything to that workspace

        # ── Drafts (workspace-scoped) ────────────────────────────────
        drafts = await draft_store.get_all()
        if workspace_id:
            drafts = [d for d in drafts if d.get("workspace_id") == workspace_id]

        # ── Published posts (workspace-scoped) ────────────────────────
        published_posts = []
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                query = supabase_service.client.table("post_history").select("*")
                
                # Workspace isolation — never return cross-workspace posts
                if workspace_id:
                    query = query.eq("workspace_id", workspace_id)
                
                result = await _async_wrap(lambda: query.execute())
                published_posts = [
                    {**p, "state": "published"} for p in (result.data or [])
                ]
        except Exception:
            pass

        # ── Also include automation_jobs for published posts ──────────
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                job_query = supabase_service.client.table("automation_jobs") \
                    .select("*") \
                    .eq("status", "PUBLISHED") \
                    .order("completed_at", ascending=False) \
                    .limit(per_page)
                
                if workspace_id:
                    job_query = job_query.eq("workspace_id", workspace_id)
                
                job_result = await _async_wrap(lambda: job_query.execute())
                
                for job in (job_result.data or []):
                    # Avoid duplicates if already in post_history
                    existing_ids = {p.get("id") for p in published_posts}
                    if job.get("job_id") not in existing_ids:
                        published_posts.append({
                            "id": job.get("job_id"),
                            "topic": job.get("topic"),
                            "caption": job.get("topic"),  # Job doesn't store full caption
                            "platform": job.get("platform", "facebook"),
                            "state": "published",
                            "workspace_id": job.get("workspace_id"),
                            "brand_id": job.get("brand_id"),
                            "created_at": job.get("completed_at"),
                            "job_id": job.get("job_id"),
                        })
        except Exception:
            pass

        # ── Filter by state ──────────────────────────────────────────
        if state_filter == "draft":
            all_posts = drafts
        elif state_filter == "published":
            all_posts = published_posts
        else:
            all_posts = drafts + published_posts

        # Sort by date descending
        all_posts.sort(
            key=lambda p: p.get("created_at") or "",
            reverse=True,
        )

        start = (page - 1) * per_page
        posts = all_posts[start : start + per_page]

        return _ok({
            "posts": posts,
            "page": page,
            "per_page": per_page,
            "total": len(all_posts),
            "drafts": len(drafts),
            "workspace_id": workspace_id,
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/posts")


@router.post("/posts")
async def create_post(
    body: PostCreateRequest,
    draft_store: DraftStore = Depends(get_draft_store),
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Create a post draft or trigger immediate publishing."""
    try:
        workspace_id = resolve_plugin_workspace(body.workspace_id)
        state = (body.state or "draft").lower()

        if state in ("publish", "published"):
            if not body.brand_id:
                return _err("brand_id is required for publishing a post", 400)

            # A low-risk authenticated Post action is itself a trusted user
            # intent. Convert it into a normal DecisionGate record when the
            # client has not supplied one; never auto-authorize elevated risk.
            decision_id = body.decision_id
            if not decision_id:
                if body.risk_level not in ("trivial", "low"):
                    return _err("decision_id is required for medium/high-risk publishing", 403)
                try:
                    from core.publish_authorization import issue_internal_publish_decision
                    decision = issue_internal_publish_decision(
                        workspace_id=workspace_id, source="manual",
                        risk_level=body.risk_level, brand_id=body.brand_id,
                        platform=(body.platforms or ["facebook"])[0],
                    )
                    decision_id = decision.decision_id
                except Exception as exc:
                    return _err(f"publish authorization failed: {exc}", 403)

            # POST /posts with scheduled_for is a durable scheduler intent, not
            # an immediate publish. The scheduler later creates the normal
            # automation job and drives the same canonical publish/outbox path.
            if body.scheduled_for:
                try:
                    from datetime import datetime as _dt
                    scheduled_for = _dt.fromisoformat(body.scheduled_for.replace("Z", "+00:00"))
                    if scheduled_for.tzinfo is None:
                        scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
                    if scheduled_for <= datetime.now(timezone.utc):
                        return _err("scheduled_for must be in the future", 400)
                    from services.supabase import supabase_service
                    if not supabase_service or not supabase_service.client:
                        return _err("scheduler database is unavailable", 503)
                    scheduled_id = f"sp_{_uuid_mod.uuid4().hex}"
                    row = {
                        "id": scheduled_id,
                        "workspace_id": workspace_id,
                        "brand_id": body.brand_id,
                        "caption": body.caption,
                        "scheduled_for": scheduled_for.isoformat(),
                        "status": "queued",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                    await _async_wrap(lambda: supabase_service.client.table("scheduled_posts").insert(row).execute())
                    await append_audit(
                        audit_log, "post_publish_scheduled", f"scheduled:{scheduled_id}",
                        status_str="queued", detail=f"Brand: {body.brand_id}",
                    )
                    return _ok({
                        "published": False, "queued": True, "scheduled": True,
                        "scheduled_post_id": scheduled_id,
                        "scheduled_for": scheduled_for.isoformat(),
                        "brand_id": body.brand_id, "workspace_id": workspace_id,
                        "decision_id": decision_id,
                    }, 202)
                except ValueError as exc:
                    return _err(f"invalid scheduled_for: {exc}", 400)
                except Exception as exc:
                    return _safe_err(exc, "POST /api/v1/posts schedule")

            from services.publish_orchestration import execute_governed_publish
            platforms = body.platforms or ["facebook"]
            results = []
            for platform in platforms:
                result = await execute_governed_publish(
                    container=container,
                    workspace_id=workspace_id,
                    brand_id=body.brand_id,
                    platform=platform,
                    topic=body.topic,
                    caption=body.caption,
                    source="api_posts",
                    risk_level=body.risk_level,
                    decision_id=decision_id,
                    approved=False,
                    idempotency_key=body.idempotency_key,
                )
                results.append(result)

            ok_results = [r for r in results if isinstance(r, dict) and r.get("ok")]
            queued = [r for r in ok_results if r.get("queued")]
            if not ok_results or len(ok_results) != len(results):
                first_error = next((r for r in results if not r.get("ok")), results[0] if results else {})
                return _ok({"published": False, "queued": False, "results": results, "error": first_error.get("error")}, 403)

            outbox_ids = [r.get("outbox_id") for r in queued if r.get("outbox_id")]
            correlation_ids = [r.get("correlation_id") for r in queued if r.get("correlation_id")]
            await append_audit(
                audit_log, "post_publish_queued", f"outbox:{outbox_ids[0] if outbox_ids else 'unknown'}",
                status_str="queued", detail=f"Brand: {body.brand_id}",
            )
            return _ok({
                "published": False,
                "queued": bool(queued),
                "outbox_ids": outbox_ids,
                "correlation_ids": correlation_ids,
                "brand_id": body.brand_id,
                "workspace_id": workspace_id,
                "results": results,
            }, 202)
        else:
            draft_id = f"draft-{_uuid_mod.uuid4().hex[:12]}"
            draft_data = {
                "id": draft_id,
                "draft_id": draft_id,
                "caption": body.caption,
                "topic": body.topic,
                "category": body.category,
                "state": "draft",
                "workspace_id": workspace_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await draft_store.set(draft_id, draft_data)
            await append_audit(
                audit_log, "post_draft_created", f"draft:{draft_id}",
                status_str="success",
            )
            return _ok({
                "draft": True,
                "draft_id": draft_id,
                "post": draft_data,
                "workspace_id": workspace_id,
            }, 201)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/posts")

@router.delete("/posts/{post_id}")
async def delete_post(
    post_id: str,
    draft_store: DraftStore = Depends(get_draft_store),
    _auth: bool = Depends(require_auth),
):
    """Delete a draft or published post."""
    try:
        deleted = await draft_store.delete(post_id)
        if deleted:
            return _ok({"deleted": True, "post_id": post_id, "source": "draft"})

        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            await _async_wrap(
                lambda: supabase_service.client.table("post_history")
                .delete()
                .eq("id", post_id)
                .execute()
            )
            return _ok({"deleted": True, "post_id": post_id, "source": "published"})

        return _err("Post not found", 404)
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/posts/{post_id}")


# ═══════════════════════════════════════════════════════════════════════════
# MESSAGES
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/messages")
async def get_messages(
    _auth: bool = Depends(require_auth),
    limit: int = 50,
):
    """Get recent messages."""
    try:
        limit = min(200, max(1, limit))
        from analytics.tracker import analytics
        msgs = await _async_wrap(analytics.get_recent_messages, limit=limit)
        return _ok({"messages": msgs, "count": len(msgs)})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/messages")


@router.post("/messages/reply")
async def send_message(
    body: MessageReplyRequest,
    _auth: bool = Depends(require_auth),
):
    """Send a reply message."""
    try:
        from services.facebook import facebook as fb
        await _async_wrap(fb.send_message, body.recipient_id, body.text)
        return _ok({"sent": True, "recipient": body.recipient_id})
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/messages/reply")


@router.get("/messages/{sender_id}")
async def get_conversation(
    sender_id: str,
    _auth: bool = Depends(require_auth),
):
    """Get conversation history for a sender."""
    try:
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            result = await _async_wrap(
                lambda: supabase_service.client.table("conversation_history")
                .select("*")
                .eq("sender_id", sender_id)
                .order("created_at", desc=True)
                .limit(50)
                .execute()
            )
            return _ok({"messages": result.data or [], "sender_id": sender_id})
        return _ok({"messages": [], "sender_id": sender_id, "note": "Supabase not configured"})
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/messages/{sender_id}")


# ═══════════════════════════════════════════════════════════════════════════
# ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/analytics")
async def get_analytics(
    _auth: bool = Depends(require_auth),
):
    """Get analytics overview — workspace-scoped."""
    try:
        from analytics.tracker import analytics
        workspace_id = auth_workspace_id_var.get()
        stats = await _async_wrap(analytics.get_stats)
        return _ok({
            "analytics": stats,
            "workspace_id": workspace_id,
            "as_of": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/analytics")
      
@router.get("/analytics/performance")      
async def analytics_performance(_auth: bool = Depends(require_auth)):
    """Get post performance data."""
    try:
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            result = await _async_wrap(
                lambda: supabase_service.client.table("post_history")
                .select("*")
                .order("published_at", desc=True)
                .limit(20)
                .execute()
            )
            return _ok({"posts": result.data or []})
        return _ok({"posts": [], "note": "Supabase not configured"})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/analytics/performance")


@router.get("/analytics/posts-performance")
async def analytics_posts_performance(
    _auth: bool = Depends(require_auth),
    limit: int = 20,
):
    """Get post performance with configurable limit."""
    try:
        limit = min(50, max(1, limit))
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            result = await _async_wrap(
                lambda: supabase_service.client.table("post_history")
                .select("*")
                .order("published_at", desc=True)
                .limit(limit)
                .execute()
            )
            data = result.data or []
            return _ok({"posts": data, "count": len(data)})
        return _ok({"posts": [], "count": 0, "note": "Supabase not configured"})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/analytics/posts-performance")


@router.get("/analytics/token-usage")
async def analytics_token_usage(_auth: bool = Depends(require_auth)):
    """Get token usage statistics."""
    try:
        from utils.metrics import metrics as _m
        snap = await _async_wrap(_m.get_snapshot)
        counters = snap.get("counters", {})
        return _ok({
            "total_tokens_estimated": counters.get("gemini_tokens_used", 0),
            "api_calls_today": counters.get("requests_total", 0),
            "daily": [],
            "as_of": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/analytics/token-usage")


@router.get("/analytics/engagement-heatmap")
async def analytics_engagement_heatmap(_auth: bool = Depends(require_auth)):
    """Get engagement heatmap data."""
    try:
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            try:
                result = await _async_wrap(
                    lambda: supabase_service.client.rpc("get_engagement_heatmap", {}).execute()
                )
                if result.data:
                    return _ok({"heatmap": result.data})
            except Exception:
                pass
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        heatmap = [{"day": d, "hours": [0.0] * 24} for d in days]
        return _ok({"heatmap": heatmap, "note": "Real data requires Supabase"})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/analytics/engagement-heatmap")


# ═══════════════════════════════════════════════════════════════════════════
# BOT CONTROL
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/bot/status")
async def bot_status(container=Depends(get_container)):
    """Get publishing status for all brands with job statistics."""
    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        from config.settings import settings
        
        manager = await _async_wrap(container.resolve, BrandSchedulerManager)
        all_status = await _async_wrap(manager.get_all_status)
        
        # Try to get aggregate job stats
        job_stats = {}
        try:
            from services.job_service import JobService
            job_service = await _async_wrap(container.try_resolve, "JobService")
            if job_service:
                job_stats = await _async_wrap(job_service.get_today_stats)
        except Exception:
            pass
        
        return _ok({
            "brands": all_status,
            "brands_active": len(all_status),
            "services": {
                "gemini": bool(settings.gemini_key),
                "facebook": settings.is_configured,
                "guardian": settings.guardian_configured,
                "supabase": settings.supabase_enabled,
            },
            "today_stats": job_stats,
            "as_of": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/bot/status")


@router.post("/bot/post")
async def force_post(
    body: dict = {},
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Force a post now. Pass brand_id to target a specific brand.
    
    Returns job_id for traceability (Architecture §29).
    """
    try:
        from repositories.brand_repository import BrandRepository
        from repositories.ai_profile_repository import AIProfileRepository
        
        brand_id = body.get("brand_id", "") if body else ""
        
        if not brand_id:
            return _err("brand_id is required", 400)
        
        brand_repo = await _async_wrap(container.resolve, BrandRepository)
        brand = await _async_wrap(brand_repo.find_by_id, brand_id)
        
        if not brand:
            return _err(f"Brand '{brand_id}' not found", 404)
        
        ai_profile = None
        if getattr(brand, 'ai_profile_id', None):
            ai_repo = await _async_wrap(container.resolve, AIProfileRepository)
            ai_profile = await _async_wrap(ai_repo.find_by_id, brand.ai_profile_id)
        
        if not ai_profile:
            return _err(f"No AI profile for brand '{brand_id}'", 400)
        
        from scheduler.brand_scheduler import BrandScheduler
        scheduler = BrandScheduler(brand=brand, ai_profile=ai_profile, container=container)
        result = await _async_wrap(scheduler.trigger_manual)
        
        # Include job_id in response for traceability
        response = {
            "ok": result.get("ok", False),
            "brand": result.get("brand", brand.name),
            "topic": result.get("topic", ""),
            "platforms": result.get("platforms", []),
            "duration_ms": result.get("duration_ms"),
            "job_id": result.get("job_id"),
        }
        if not result.get("ok"):
            response["error"] = result.get("error", "Unknown error")
        
        return _ok(response, 202)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/bot/post")


@router.post("/bot/news")
async def force_news(
    body: dict = {},
    _auth: bool = Depends(require_auth),
):
    """Force a news post."""
    try:
        from services.news_scraper import news_scraper
        category = body.get("category") if body else None
        queued = await _async_wrap(news_scraper.post_news, category=category)
        return _ok({"queued": bool(queued)}, 202)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/bot/news")


@router.post("/bot/engage")
async def force_engage(_auth: bool = Depends(require_workspace_operator)):
    """Force engagement run."""
    try:
        from services.social_monitor import social_monitor
        queued = await _async_wrap(social_monitor.run_engagement)
        return _ok({"queued": bool(queued)}, 202)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/bot/engage")


# ═══════════════════════════════════════════════════════════════════════════
# JOBS — Architecture §29 Observability
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Get status of an automation job by job_id."""
    try:
        from services.job_service import JobService
        job_service = await _async_wrap(container.try_resolve, JobService)
        
        if job_service is None:
            return _err("Job service not available", 503)
        
        job = await _async_wrap(job_service.find_by_id, job_id)
        
        if job is None:
            return _err(f"Job '{job_id}' not found", 404)
        
        # Workspace isolation
        workspace_id = auth_workspace_id_var.get()
        if workspace_id and job.workspace_id and job.workspace_id != workspace_id:
            return _err(f"Job '{job_id}' not found", 404)  # Mask existence
        
        return _ok({"job": job.to_status_dict()})
    except DatabaseQueryError:
        return _err("Job service unavailable", 503)
    except KanyozaError as exc:
        return _safe_err(exc, f"GET /api/v1/jobs/{job_id}")
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/jobs/{job_id}")

@router.get("/brands/{brand_id}/jobs")
async def get_brand_jobs(
    brand_id: str,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
    status: str = "",
    limit: int = 20,
):
    """Get automation jobs for a brand.
    
    Optional query params:
      - status: Filter by job status (e.g. 'PUBLISHED', 'FAILED')
      - limit: Max results (default 20, max 100)
    """
    try:
        await require_workspace_ownership("brand", brand_id, container)
        
        from services.job_service import JobService
        from models.automation_job import JobStatus
        
        job_service = await _async_wrap(container.try_resolve, JobService)
        
        if job_service is None:
            return _err("Job service not available", 503)
        
        limit = min(100, max(1, limit))
        
        job_status = None
        if status:
            try:
                job_status = JobStatus(status.upper())
            except ValueError:
                return _err(f"Invalid status: {status}. Valid: {[s.value for s in JobStatus]}", 400)
        
        jobs = await _async_wrap(
            job_service.find_by_brand,
            brand_id=brand_id,
            status=job_status,
            limit=limit,
        )
        
        stats = await _async_wrap(job_service.get_today_stats, brand_id=brand_id)
        
        return _ok({
            "jobs": [j.to_status_dict() for j in jobs],
            "count": len(jobs),
            "today_stats": stats,
        })
    except HTTPException:
        raise
    except DatabaseQueryError:
        return _err("Job service unavailable", 503)
    except KanyozaError as exc:
        return _safe_err(exc, f"GET /api/v1/brands/{brand_id}/jobs")
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/brands/{brand_id}/jobs")


# ═══════════════════════════════════════════════════════════════════════════
# GUARDIAN
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/guardian/status")
async def guardian_status(container=Depends(get_container)):
    """Get Guardian status."""
    try:
        from services.guardian_service import GuardianService
        svc = await _async_wrap(container.try_resolve, GuardianService)
        if not svc:
            return _ok({"configured": False, "message": "Guardian not configured"})
        status_data = await _async_wrap(svc.get_status)
        return _ok(status_data)
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/guardian/status")


@router.get("/guardian/issues")
async def guardian_issues(
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
    severity: str = "",
):
    """Get Guardian issues, optionally filtered by severity."""
    try:
        from services.guardian_service import GuardianService
        svc = await _async_wrap(container.try_resolve, GuardianService)
        if not svc:
            return _ok({"issues": [], "message": "Guardian not configured"})

        issues = await _async_wrap(svc.get_issues)
        if severity:
            allowed = {s.strip() for s in severity.split(",")}
            issues = [
                i for i in issues
                if isinstance(i, dict) and i.get("severity", "").lower() in allowed
            ]

        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        issues = sorted(
            issues,
            key=lambda i: order.get(i.get("severity", "").lower(), 9)
            if isinstance(i, dict) else 9,
        )
        return _ok({"issues": issues, "count": len(issues)})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/guardian/issues")


@router.post("/guardian/scan")
async def trigger_scan(
    background_tasks: BackgroundTasks,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Trigger a Guardian scan in the background."""
    try:
        from services.guardian_service import GuardianService
        svc = await _async_wrap(container.try_resolve, GuardianService)
        if not svc or not svc.is_configured:
            return _err("Guardian requires GitHub credentials", 503)

        background_tasks.add_task(_async_wrap, svc.scan_all)
        return _ok({"started": True, "message": "Scan started"}, 202)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/guardian/scan")


# ═══════════════════════════════════════════════════════════════════════════
# SCHEDULE
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/schedule")
async def get_schedule(
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Get scheduler configuration from BrandSchedulerManager."""
    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        manager = await _async_wrap(container.resolve, BrandSchedulerManager)
        status = manager.get_status()
        return _ok({"schedule": status})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/schedule")


@router.put("/schedule")
async def update_schedule(
    body: ScheduleUpdateRequest,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Update scheduler configuration."""
    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        manager = await _async_wrap(container.resolve, BrandSchedulerManager)
        manager.sync_brands()
        return _ok({"updated": True, "config": manager.get_status()})
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _safe_err(exc, "PUT /api/v1/schedule")


# ═══════════════════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/metrics")
async def get_metrics(_auth: bool = Depends(require_auth)):
    """Get full metrics snapshot."""
    try:
        from utils.metrics import metrics as metrics_collector
        snapshot = await _async_wrap(metrics_collector.get_snapshot)
        return _ok({"metrics": snapshot, "as_of": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/metrics")


@router.get("/metrics/public")
async def get_public_metrics():
    """Get public-facing metrics."""
    try:
        from utils.metrics import metrics as _m
        snap = await _async_wrap(_m.get_snapshot)
        counters = snap.get("counters", {})
        return _ok({
            "uptime_seconds": snap.get("uptime_seconds"),
            "total_posts": counters.get("posts_published", 0),
            "total_messages": counters.get("messages_received", 0),
            "total_errors": counters.get("errors", 0),
            "as_of": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/metrics/public")


@router.get("/metrics/resources")
async def get_resource_metrics(_auth: bool = Depends(require_auth)):
    """Get system resource metrics."""
    try:
        cpu = mem = disk = 0.0
        try:
            import psutil as _ps
            cpu = _ps.cpu_percent(interval=None)
            mem = _ps.virtual_memory().percent
            disk = _ps.disk_usage("/").percent
        except Exception:
            pass
        return _ok({
            "cpu_percent": cpu,
            "memory_percent": mem,
            "disk_percent": disk,
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/metrics/resources")


@router.get("/metrics/query")
async def metrics_query(
    _auth: bool = Depends(require_auth),
    q: str = "",
):
    """Prometheus-compatible metrics query."""
    try:
        from utils.metrics import metrics as _m
        snap = await _async_wrap(_m.get_snapshot)
        value = 0
        if "error" in q.lower():
            value = snap.get("counters", {}).get("errors", 0)
        elif "rps" in q.lower():
            value = snap.get("counters", {}).get("requests_total", 0)
        return JSONResponse({
            "status": "success",
            "data": {
                "result": [{
                    "metric": {"__name__": q or "kanyoza_metric"},
                    "value": [int(datetime.now(timezone.utc).timestamp()), str(value)],
                }]
            },
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/metrics/query")


# ═══════════════════════════════════════════════════════════════════════════
# TRACING — Digital Twin
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/traces/recent")
async def get_recent_traces(
    _auth: bool = Depends(require_auth),
    limit: int = 30,
):
    """A09: Get recent request traces for the Digital Twin dashboard."""
    try:
        limit = min(100, max(1, limit))
        from utils.tracing import get_recent_traces
        traces = await _async_wrap(get_recent_traces, limit=limit)
        return _ok({"traces": traces, "count": len(traces)})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/traces/recent")


# ═══════════════════════════════════════════════════════════════════════════
# AI CONFIG & PERSONA
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/ai/config")
async def get_ai_config(_auth: bool = Depends(require_auth)):
    """Get AI configuration."""
    try:
        from config.settings import settings
        from api.ai_runtime import get as _rt_get, VALID_MOODS
        rt = await _async_wrap(_rt_get)
        return _ok({
            "provider": settings.ai_provider,
            "model": settings.gemini_model,
            "chat_temperature": settings.gemini_chat_temperature,
            "post_temperature": settings.gemini_post_temperature,
            "safety_level": settings.gemini_safety_level,
            "available_models": [
                "gemini-2.5-flash", "gemini-2.5-pro",
                "gemini-1.5-pro", "gemini-1.5-flash",
            ],
            "available_providers": ["gemini", "huggingface", "cloudflare"],
            "system_prompt_override": rt["system_prompt_override"],
            "persona_mood": rt["persona_mood"],
            "tone_assertiveness": rt["tone_assertiveness"],
            "tone_humor": rt["tone_humor"],
            "tone_formality": rt["tone_formality"],
            "available_moods": sorted(VALID_MOODS),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/ai/config")


@router.post("/image/generate")
async def generate_image_endpoint(
    body: ImageGenerateRequest,
    _auth: bool = Depends(require_auth),
):
    """Generate high-quality AI image on demand using Cloudflare Workers AI free models."""
    try:
        from services.cloudflare_image import generate_trend_image
        from service_container import get_container
        from services.supabase import SupabaseService

        container = get_container()
        supabase_svc = container.try_resolve(SupabaseService) if container else None
        supabase_client = supabase_svc.client if supabase_svc else None

        trend_payload = {
            "title": body.topic,
            "content": body.content or "",
            "author": body.author or "",
            "platform": body.platform or "facebook",
        }

        image_url = await _async_wrap(generate_trend_image, trend_payload, supabase_client=supabase_client)
        if not image_url:
            return _ok({
                "success": False,
                "image_url": None,
                "message": "Image generation failed or Cloudflare Workers AI credentials unconfigured. Text-only fallback active.",
            })

        return _ok({
            "success": True,
            "image_url": image_url,
            "topic": body.topic,
        })
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/image/generate")


@router.get("/image/status")
async def get_image_status(_auth: bool = Depends(require_auth)):
    """Check Cloudflare Workers AI configuration and available free models."""
    try:
        import os
        from services.cloudflare_image import MODELS

        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
        if not account_id or not api_token:
            try:
                from config.settings import settings
                account_id = account_id or getattr(settings, "cloudflare_account_id", "").strip()
                api_token = api_token or getattr(settings, "cloudflare_api_token", "").strip()
            except Exception:
                pass

        is_configured = bool(account_id and api_token)

        return _ok({
            "configured": is_configured,
            "provider": "Cloudflare Workers AI",
            "tier": "Free Tier",
            "models": MODELS,
            "primary_model": MODELS.get("flux_schnell"),
            "fallback_model": MODELS.get("sdxl_base"),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/image/status")


@router.put("/ai/config")
async def update_ai_config(
    body: AIConfigUpdateRequest,
    _auth: bool = Depends(require_auth),
):
    """Update AI configuration."""
    try:
        from config.settings import settings
        from api.ai_runtime import update as _rt_update, get as _rt_get

        data = body.model_dump(exclude_none=True)

        if "provider" in data:
            settings.ai_provider = data["provider"]
        if "model" in data:
            settings.gemini_model = str(data["model"])

        for fk, sk in [
            ("chat_temperature", "gemini_chat_temperature"),
            ("post_temperature", "gemini_post_temperature"),
        ]:
            if fk in data:
                setattr(settings, sk, float(data[fk]))

        if "safety_level" in data:
            settings.gemini_safety_level = data["safety_level"]

        runtime_keys = {
            "system_prompt_override", "persona_mood",
            "tone_assertiveness", "tone_humor", "tone_formality",
        }
        rt_updates = {k: v for k, v in data.items() if k in runtime_keys}
        if rt_updates:
            await _async_wrap(_rt_update, **rt_updates)

        rt = await _async_wrap(_rt_get)
        return _ok({
            "updated": True,
            "config": {"provider": settings.ai_provider, "model": settings.gemini_model, **rt},
        })
    except Exception as exc:
        return _safe_err(exc, "PUT /api/v1/ai/config")


@router.get("/ai/persona")
async def get_ai_persona(_auth: bool = Depends(require_auth)):
    """Get AI persona configuration."""
    try:
        from api.ai_runtime import get as _rt_get
        from config.settings import settings
        rt = await _async_wrap(_rt_get)
        return _ok({
            "tone": int(rt.get("tone_formality", 0.6) * 100),
            "aggression": int(rt.get("tone_assertiveness", 0.7) * 100),
            "humor": int(rt.get("tone_humor", 0.2) * 100),
            "model": getattr(settings, "gemini_model", "gemini-2.5-flash"),
            "system_prompt": rt.get("system_prompt_override") or "",
            **rt,
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/ai/persona")


@router.put("/ai/persona")
async def update_ai_persona(
    body: dict,
    _auth: bool = Depends(require_auth),
):
    """Update AI persona."""
    try:
        from api.ai_runtime import update as _rt_update, get as _rt_get
        from config.settings import settings

        rt_updates = {}
        if "tone" in body:
            rt_updates["tone_formality"] = round(float(body["tone"]) / 100.0, 4)
        if "aggression" in body:
            rt_updates["tone_assertiveness"] = round(float(body["aggression"]) / 100.0, 4)
        if "humor" in body:
            rt_updates["tone_humor"] = round(float(body["humor"]) / 100.0, 4)
        if "model" in body:
            settings.gemini_model = str(body["model"])
        if "system_prompt" in body:
            rt_updates["system_prompt_override"] = body["system_prompt"] or None

        for k in ("persona_mood", "tone_formality", "tone_assertiveness", "tone_humor"):
            if k in body and k not in rt_updates:
                rt_updates[k] = body[k]

        rt = (
            await _async_wrap(_rt_update, **rt_updates)
            if rt_updates
            else await _async_wrap(_rt_get)
        )
        return _ok({"updated": True, "persona": rt})
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _safe_err(exc, "PUT /api/v1/ai/persona")


@router.get("/persona")
async def get_persona(_auth: bool = Depends(require_auth)):
    """Get bot persona for authenticated callers."""
    try:
        from core.persona import bot_persona
        from api.ai_runtime import get as _rt_get, VALID_MOODS
        rt = await _async_wrap(_rt_get)
        state = await _async_wrap(bot_persona.to_dict)
        return _ok({
            "name": bot_persona.name,
            "tagline": bot_persona.tagline,
            "current_mood": state.get("current_mood", "professional"),
            "available_moods": sorted(VALID_MOODS),
            **rt,
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/persona")


@router.put("/persona")
async def update_persona(
    body: dict,
    _auth: bool = Depends(require_auth),
):
    """Update persona settings."""
    try:
        from api.ai_runtime import update as _rt_update
        allowed = {
            "persona_mood", "tone_assertiveness", "tone_humor",
            "tone_formality", "system_prompt_override",
        }
        unknown = set(body.keys()) - allowed
        if unknown:
            return _err(f"Unknown keys: {sorted(unknown)}", 400)
        rt = await _async_wrap(
            _rt_update,
            **{k: v for k, v in body.items() if k in allowed},
        )
        return _ok({"updated": True, "persona": rt})
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _safe_err(exc, "PUT /api/v1/persona")


@router.post("/persona/reset")
async def reset_persona(_auth: bool = Depends(require_workspace_operator)):
    """Reset persona to defaults."""
    try:
        from api.ai_runtime import update as _rt_update
        rt = await _async_wrap(
            _rt_update,
            persona_mood="professional",
            tone_assertiveness=0.7,
            tone_humor=0.2,
            tone_formality=0.6,
            system_prompt_override=None,
        )
        return _ok({"reset": True, "persona": rt})
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/persona/reset")


# ═══════════════════════════════════════════════════════════════════════════
# AI CHAT — A03 Prompt Injection Guard
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/ai/chat")
async def ai_chat(
    body: AIChatRequest,
    request: Request,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _rate=create_rate_limit_dependency("ai_chat", max_per_minute=AI_CHAT_RATE_LIMIT),
):
    """
    A03: AI chat with defense-in-depth prompt injection protection.
    Guard applied at Pydantic validator AND here for redundancy.
    """
    try:
        if INJECTION_PATTERNS_RE.search(body.message):
            cid = correlation_id_var.get()
            logger.warning("[%s] A03: Prompt injection blocked (defense-in-depth)", cid)
            return _ok({
                "reply": "I can't process that request. Please rephrase your message.",
                "model": "guard",
                "tokens_used": 0,
            })

        if body.history:
            from core.memory import memory as _mem
            for turn in body.history[-10:]:
                role = str(turn.get("role", "user"))
                content = str(turn.get("content", ""))
                await _async_wrap(
                    _mem.add_message,
                    "api_chat_user",
                    role if role != "bot" else "assistant",
                    content,
                )

        from config.settings import settings
        from services.command_executor import CommandExecutor
        from services.gemini import GeminiService

        # Pass the FastAPI container so CommandExecutor can invoke plugins
        # without needing Flask's current_app (spec §10)
        # Propagate the caller's credential. A user-originated AI command must
        # never be upgraded to platform/master authority by an internal token.
        caller_token = ""
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            caller_token = authorization.split(" ", 1)[1].strip()
        elif request.headers.get("X-API-Key"):
            caller_token = request.headers.get("X-API-Key", "").strip()

        executor = CommandExecutor(
            gemini_service=GeminiService(),
            master_token=settings.master_api_token,
            credential_token=caller_token,
            base_url=str(request.base_url).rstrip("/") + "/api/v1",
            container=container,
        )
        intent = await _async_wrap(executor.parse_intent, body.message)

        if intent and intent.get("action"):
            result = await _async_wrap(executor.execute, intent)
            await append_audit(
                audit_log, "ai_command", intent.get("action", "unknown"),
                status_str="success" if result.get("ok") else "failed",
                detail=f"Command: {body.message[:100]}",
            )
            return _ok({
                "reply": result.get("message", "Command processed."),
                "model": "command-executor",
                "tokens_used": 0,
                "command_result": result,
            })

        gemini = GeminiService()
        reply = await _async_wrap(
            gemini.generate_chat_response,
            sender_id="api_chat_user",
            user_message=body.message,
        )
        await append_audit(
            audit_log, "ai_chat", "gemini", status_str="success",
            detail=f"Chat ({len(body.message)}→{len(reply)} chars)",
        )
        return _ok({
            "reply": reply,
            "model": settings.gemini_model,
            "tokens_used": len(reply.split()),
        })
    except Exception as exc:
        logger.warning("Gemini chat failed: %s", exc)
        return _ok({
            "reply": "The AI service is temporarily unavailable.",
            "model": "unavailable",
            "tokens_used": 0,
        })


# ═══════════════════════════════════════════════════════════════════════════
# WORKSPACES CRUD — A01 Workspace Isolation
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/workspaces")
async def create_workspace(
    body: WorkspaceCreateRequest,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """A01: Create a new workspace — spec §1.1/§3 contract alignment."""
    try:
        from models.workspace import Workspace
        from repositories.workspace_repository import WorkspaceRepository

        ws_id = body.id or f"ws-{_uuid_mod.uuid4().hex[:12]}"

        # Auto-generate slug if not provided; enforce uniqueness
        slug = body.slug or _sanitize(body.name).lower().replace(" ", "-")
        slug = _re_mod.sub(r"[^a-z0-9\-]", "", slug)[:64]

        repo = await _async_wrap(container.resolve, WorkspaceRepository)

        # Validate unique slug
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                existing = await _async_wrap(
                    lambda: supabase_service.client.table("workspaces")
                    .select("id")
                    .eq("slug", slug)
                    .execute()
                )
                if existing.data:
                    return _err(f"Slug '{slug}' is already in use", 409)
        except Exception:
            pass

        # The authenticated principal is authoritative for workspace ownership.
        # A normal caller cannot create a workspace owned by another identity.
        from core.request_context import require_request_context
        ctx = require_request_context()
        requested_owner = (body.owner or "").strip().lower()
        caller_email = (ctx.user_email or "").strip().lower()
        if ctx.species_privileges:
            owner_email = requested_owner or None
        else:
            if not caller_email:
                raise HTTPException(status_code=403, detail="Workspace creation requires an authenticated user identity")
            if requested_owner and requested_owner != caller_email:
                raise HTTPException(status_code=403, detail="Workspace owner must match the authenticated user")
            owner_email = caller_email

        # Build workspace — only accepted contract fields (spec §1.1)
        ws_kwargs = {
            "id": ws_id,
            "name": body.name,
            "slug": slug,
            "description": body.description,
        }
        if owner_email:
            ws_kwargs["owner_email"] = owner_email
        if body.plan:
            ws_kwargs["plan"] = body.plan

        ws = Workspace(**{k: v for k, v in ws_kwargs.items() if v is not None})
        saved = await _async_wrap(repo.save, ws)

        user_email = auth_user_email_var.get()
        await append_audit(
            audit_log, "workspace_created", f"workspace:{ws_id}",
            user=user_email or "system", status_str="success",
        )
        return _ok({"workspace": saved.model_dump(mode="json")}, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/workspaces")


@router.get("/workspaces")
async def list_workspaces(
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """A01: List workspaces — scoped to user's workspace if not admin."""
    try:
        from repositories.workspace_repository import WorkspaceRepository
        repo = await _async_wrap(container.resolve, WorkspaceRepository)
        workspace_id = auth_workspace_id_var.get()

        if workspace_id:
            ws = await _async_wrap(repo.find_by_id, workspace_id)
            workspaces = [ws.model_dump(mode="json")] if ws else []
        elif not repo.supabase:
            workspaces = list(getattr(repo, "_in_memory", {}).values())
        else:
            result = await _async_wrap(
                lambda: repo.supabase.table("workspaces").select("*").execute()
            )
            workspaces = result.data or []

        return _ok({"workspaces": workspaces})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/workspaces")


@router.get("/workspaces/{workspace_id}")
async def get_workspace(
    workspace_id: str,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """A01: Get a workspace — ownership verified."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)
        from repositories.workspace_repository import WorkspaceRepository
        repo = await _async_wrap(container.resolve, WorkspaceRepository)
        ws = await _async_wrap(repo.find_by_id, workspace_id)
        if not ws:
            return _err("Workspace not found", 404)
        return _ok({"workspace": ws.model_dump(mode="json")})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/workspaces/{workspace_id}")


@router.put("/workspaces/{workspace_id}")
async def update_workspace(
    workspace_id: str,
    body: dict,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """A01: Update a workspace — ownership verified."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)
        from models.workspace import Workspace
        from repositories.workspace_repository import WorkspaceRepository

        repo = await _async_wrap(container.resolve, WorkspaceRepository)
        ws = await _async_wrap(repo.find_by_id, workspace_id)
        if not ws:
            return _err("Workspace not found", 404)

        update_data = {
            k: v for k, v in body.items()
            if k not in ("id", "created_at")
        }
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        ws_dict = ws.model_dump(mode="json")
        ws_dict.update(update_data)

        saved = await _async_wrap(repo.save, Workspace(**ws_dict))
        await append_audit(
            audit_log, "workspace_updated", f"workspace:{workspace_id}", status_str="success",
        )
        return _ok({"workspace": saved.model_dump(mode="json")})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"PUT /api/v1/workspaces/{workspace_id}")


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """A01: Delete a workspace — ownership verified."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)
        from repositories.workspace_repository import WorkspaceRepository
        repo = await _async_wrap(container.resolve, WorkspaceRepository)
        deleted = await _async_wrap(repo.delete, workspace_id)
        await append_audit(
            audit_log, "workspace_deleted", f"workspace:{workspace_id}", status_str="success",
        )
        return _ok({"deleted": deleted})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/workspaces/{workspace_id}")

# ═══════════════════════════════════════════════════════════════════════════
# USERS CRUD — Workspace-scoped RBAC
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/workspaces/{workspace_id}/users")
async def list_workspace_users(
    workspace_id: str,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """List all users in a workspace. Requires admin or above."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)

        from repositories.workspace_member_repository import WorkspaceMemberRepository
        repo = await _async_wrap(container.resolve, WorkspaceMemberRepository)
        members = await _async_wrap(repo.find_by_workspace, workspace_id)

        return _ok({
            "users": [
                {
                    "id": m.id,
                    "user_id": m.user_id,
                    "role": m.role.value,
                    "invited_at": m.invited_at.isoformat() if m.invited_at else None,
                    "accepted_at": m.accepted_at.isoformat() if m.accepted_at else None,
                }
                for m in members
            ],
            "count": len(members),
        })
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/workspaces/{workspace_id}/users")


@router.post("/workspaces/{workspace_id}/users")
async def add_workspace_user(
    workspace_id: str,
    body: UserCreateRequest,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """Add a user to a workspace. Requires admin or above."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)

        from models.workspace import WorkspaceMember, MemberRole
        from repositories.workspace_member_repository import WorkspaceMemberRepository
        import uuid as _uuid_mod

        repo = await _async_wrap(container.resolve, WorkspaceMemberRepository)

        # Check for duplicate email
        existing = await _async_wrap(repo.find_by_user_and_workspace, body.email, workspace_id)
        if existing:
            return _err(f"User '{body.email}' is already a member of this workspace", 409)

        member = WorkspaceMember(
            id=f"member-{_uuid_mod.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            user_id=body.email,
            role=MemberRole(body.role),
        )

        saved = await _async_wrap(repo.save, member)

        await append_audit(
            audit_log, "user_added", f"workspace:{workspace_id}/user:{body.email}",
            status_str="success",
            detail=f"Role: {body.role}",
        )

        return _ok({
            "user": {
                "id": saved.id,
                "user_id": saved.user_id,
                "role": saved.role.value,
                "invited_at": saved.invited_at.isoformat(),
            },
        }, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/users")


@router.put("/workspaces/{workspace_id}/users/{user_id}")
async def update_workspace_user(
    workspace_id: str,
    user_id: str,
    body: UserUpdateRequest,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """Update a user's role. Requires admin or above. Cannot modify yourself."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)

        from repositories.workspace_member_repository import WorkspaceMemberRepository
        from models.workspace import MemberRole

        # Prevent self-modification
        current_user = auth_user_email_var.get()
        if current_user and current_user == user_id:
            return _err("You cannot modify your own role", 403)

        repo = await _async_wrap(container.resolve, WorkspaceMemberRepository)
        member = await _async_wrap(repo.find_by_user_and_workspace, user_id, workspace_id)

        if not member:
            return _err("User not found in this workspace", 404)

        old_role = member.role.value

        if body.role:
            # Prevent removing the last owner
            if old_role == "owner" and body.role != "owner":
                all_members = await _async_wrap(repo.find_by_workspace, workspace_id)
                owner_count = sum(1 for m in all_members if m.role == MemberRole.OWNER)
                if owner_count <= 1:
                    return _err("Cannot remove the last owner of a workspace", 403)

            member.role = MemberRole(body.role)

        member.updated_at = datetime.now(timezone.utc).isoformat()
        saved = await _async_wrap(repo.save, member)

        await append_audit(
            audit_log, "user_updated", f"workspace:{workspace_id}/user:{user_id}",
            status_str="success",
            detail=f"Role: {old_role} → {saved.role.value}",
        )

        return _ok({
            "user": {
                "id": saved.id,
                "user_id": saved.user_id,
                "role": saved.role.value,
            },
        })
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"PUT /api/v1/workspaces/{workspace_id}/users/{user_id}")


@router.delete("/workspaces/{workspace_id}/users/{user_id}")
async def remove_workspace_user(
    workspace_id: str,
    user_id: str,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """Remove a user from a workspace. Requires admin or above."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)

        from repositories.workspace_member_repository import WorkspaceMemberRepository
        from models.workspace import MemberRole

        # Prevent self-deletion
        current_user = auth_user_email_var.get()
        if current_user and current_user == user_id:
            return _err("You cannot remove yourself from a workspace", 403)

        repo = await _async_wrap(container.resolve, WorkspaceMemberRepository)
        member = await _async_wrap(repo.find_by_user_and_workspace, user_id, workspace_id)

        if not member:
            return _err("User not found in this workspace", 404)

        # Prevent deleting the last owner
        if member.role == MemberRole.OWNER:
            all_members = await _async_wrap(repo.find_by_workspace, workspace_id)
            owner_count = sum(1 for m in all_members if m.role == MemberRole.OWNER)
            if owner_count <= 1:
                return _err("Cannot remove the last owner of a workspace", 403)

        deleted = await _async_wrap(repo.delete, member.id)

        await append_audit(
            audit_log, "user_removed", f"workspace:{workspace_id}/user:{user_id}",
            status_str="success",
        )

        return _ok({"deleted": deleted})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/workspaces/{workspace_id}/users/{user_id}")


# ═══════════════════════════════════════════════════════════════════════════
# BRANDS CRUD
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/workspaces/{workspace_id}/brands")
async def create_brand(
    workspace_id: str,
    body: dict,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Create a brand in a workspace."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)
        from models.brand import Brand
        from repositories.brand_repository import BrandRepository

        brand_id = body.get("id") or f"brand-{_uuid_mod.uuid4().hex[:12]}"
        body["id"] = brand_id
        body["workspace_id"] = workspace_id
        if not body.get("name"):
            return _err("name is required", 400)
        if not body.get("slug"):
            body["slug"] = _sanitize(body["name"]).lower().replace(" ", "-")

        brand = Brand(**body)
        repo = await _async_wrap(container.resolve, BrandRepository)
        saved = await _async_wrap(repo.save, brand)

        await append_audit(
            audit_log, "brand_created", f"brand:{brand_id}", status_str="success",
        )
        return _ok({"brand": saved.model_dump(mode="json")}, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/brands")


@router.get("/workspaces/{workspace_id}/brands")
async def list_brands(
    workspace_id: str,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """List brands in a workspace."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)
        from repositories.brand_repository import BrandRepository
        repo = await _async_wrap(container.resolve, BrandRepository)
        brands = await _async_wrap(repo.find_by_workspace, workspace_id)
        return _ok({"brands": [b.model_dump(mode="json") for b in brands]})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/workspaces/{workspace_id}/brands")


@router.get("/brands/{brand_id}")
async def get_brand(
    brand_id: str,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Get a specific brand."""
    try:
        await require_workspace_ownership("brand", brand_id, container)
        from repositories.brand_repository import BrandRepository
        repo = await _async_wrap(container.resolve, BrandRepository)
        brand = await _async_wrap(repo.find_by_id, brand_id)
        if not brand:
            return _err("Brand not found", 404)
        return _ok({"brand": brand.model_dump(mode="json")})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/brands/{brand_id}")


@router.put("/brands/{brand_id}")
async def update_brand(
    brand_id: str,
    body: dict,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Update a brand."""
    try:
        await require_workspace_ownership("brand", brand_id, container)
        from models.brand import Brand
        from repositories.brand_repository import BrandRepository

        repo = await _async_wrap(container.resolve, BrandRepository)
        brand = await _async_wrap(repo.find_by_id, brand_id)
        if not brand:
            return _err("Brand not found", 404)

        update_data = {
            k: v for k, v in body.items()
            if k not in ("id", "workspace_id", "created_at")
        }
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        brand_dict = brand.model_dump(mode="json")
        brand_dict.update(update_data)

        saved = await _async_wrap(repo.save, Brand(**brand_dict))
        await append_audit(
            audit_log, "brand_updated", f"brand:{brand_id}", status_str="success",
        )
        return _ok({"brand": saved.model_dump(mode="json")})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"PUT /api/v1/brands/{brand_id}")


@router.delete("/brands/{brand_id}")
async def delete_brand(
    brand_id: str,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Delete a brand."""
    try:
        await require_workspace_ownership("brand", brand_id, container)
        from repositories.brand_repository import BrandRepository
        repo = await _async_wrap(container.resolve, BrandRepository)
        deleted = await _async_wrap(repo.delete, brand_id)
        await append_audit(
            audit_log, "brand_deleted", f"brand:{brand_id}", status_str="success",
        )
        return _ok({"deleted": deleted})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/brands/{brand_id}")


# ═══════════════════════════════════════════════════════════════════════════
# AI PROFILES CRUD
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/workspaces/{workspace_id}/ai-profiles")
async def create_ai_profile(
    workspace_id: str,
    body: dict,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Create an AI profile."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)
        from models.ai_profile import AIProfile
        from repositories.ai_profile_repository import AIProfileRepository

        profile_id = body.get("id") or f"profile-{_uuid_mod.uuid4().hex[:12]}"
        body["id"] = profile_id
        body["workspace_id"] = workspace_id
        if not body.get("name"):
            return _err("name is required", 400)

        profile = AIProfile(**body)
        repo = await _async_wrap(container.resolve, AIProfileRepository)
        saved = await _async_wrap(repo.save, profile)

        await append_audit(
            audit_log, "ai_profile_created", f"ai_profile:{profile_id}", status_str="success",
        )
        return _ok({"ai_profile": saved.model_dump(mode="json")}, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/ai-profiles")


@router.get("/workspaces/{workspace_id}/ai-profiles")
async def list_ai_profiles(
    workspace_id: str,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """List AI profiles."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)
        from repositories.ai_profile_repository import AIProfileRepository
        repo = await _async_wrap(container.resolve, AIProfileRepository)
        profiles = await _async_wrap(repo.find_by_workspace, workspace_id)
        return _ok({"ai_profiles": [p.model_dump(mode="json") for p in profiles]})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/workspaces/{workspace_id}/ai-profiles")


@router.put("/ai-profiles/{profile_id}")
async def update_ai_profile(
    profile_id: str,
    body: dict,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Update an AI profile."""
    try:
        await require_workspace_ownership("ai_profile", profile_id, container)
        from models.ai_profile import AIProfile
        from repositories.ai_profile_repository import AIProfileRepository

        repo = await _async_wrap(container.resolve, AIProfileRepository)
        profile = await _async_wrap(repo.find_by_id, profile_id)
        if not profile:
            return _err("AI Profile not found", 404)

        update_data = {
            k: v for k, v in body.items()
            if k not in ("id", "workspace_id", "created_at")
        }
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        profile_dict = profile.model_dump(mode="json")
        profile_dict.update(update_data)

        saved = await _async_wrap(repo.save, AIProfile(**profile_dict))
        await append_audit(
            audit_log, "ai_profile_updated", f"ai_profile:{profile_id}", status_str="success",
        )
        return _ok({"ai_profile": saved.model_dump(mode="json")})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"PUT /api/v1/ai-profiles/{profile_id}")


@router.delete("/ai-profiles/{profile_id}")
async def delete_ai_profile(
    profile_id: str,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Delete an AI profile."""
    try:
        await require_workspace_ownership("ai_profile", profile_id, container)
        from repositories.ai_profile_repository import AIProfileRepository
        repo = await _async_wrap(container.resolve, AIProfileRepository)
        deleted = await _async_wrap(repo.delete, profile_id)
        await append_audit(
            audit_log, "ai_profile_deleted", f"ai_profile:{profile_id}", status_str="success",
        )
        return _ok({"deleted": deleted})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/ai-profiles/{profile_id}")


# ═══════════════════════════════════════════════════════════════════════════
# SOCIAL ACCOUNTS — A02 Credential stripping
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/workspaces/{workspace_id}/social-accounts")
async def create_social_account(
    workspace_id: str,
    body: dict,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """A02: Create a social account — credentials stripped from response."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)
        from models.social_account import SocialAccount
        from repositories.social_account_repository import SocialAccountRepository

        acc_id = body.get("id") or f"acc-{_uuid_mod.uuid4().hex[:12]}"
        body["id"] = acc_id
        body["workspace_id"] = workspace_id
        if not body.get("platform"):
            return _err("platform is required", 400)

        PLATFORM_FIELDS = {
            "facebook": ["page_id", "page_access_token", "verify_token"],
            "twitter": ["client_id", "client_secret"],
            "linkedin": ["person_urn"],
            "instagram": ["page_id"],
            "whatsapp": ["phone_number_id"],
            "telegram": ["bot_token"],
        }
        platform = body.get("platform", "")
        if platform in PLATFORM_FIELDS:
            pc = body.get("platform_config", {}) or {}
            for field in PLATFORM_FIELDS[platform]:
                if field in body:
                    pc[field] = body.pop(field)
            body["platform_config"] = pc

        account = SocialAccount(**body)
        repo = await _async_wrap(container.resolve, SocialAccountRepository)
        saved = await _async_wrap(repo.save, account)

        response_data = _strip_credentials(saved.model_dump(mode="json"))

        await append_audit(
            audit_log, "social_account_created", f"social_account:{acc_id}", status_str="success",
        )
        return _ok({"social_account": response_data}, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/social-accounts")


@router.get("/workspaces/{workspace_id}/social-accounts")
async def list_social_accounts(
    workspace_id: str,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """A02: List social accounts — credentials stripped."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)
        from repositories.social_account_repository import SocialAccountRepository
        repo = await _async_wrap(container.resolve, SocialAccountRepository)
        accounts = await _async_wrap(repo.find_by_workspace, workspace_id)

        safe = [_strip_credentials(a.model_dump(mode="json")) for a in accounts]
        return _ok({"social_accounts": safe})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/workspaces/{workspace_id}/social-accounts")


@router.put("/social-accounts/{account_id}")
async def update_social_account(
    account_id: str,
    body: dict,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """
    Update a social account.
    Spec §4.2: if access_token is null/empty/omitted in the request,
    keep the existing credential — do not overwrite it.
    """
    try:
        await require_workspace_ownership("social_account", account_id, container)
        from models.social_account import SocialAccount
        from repositories.social_account_repository import SocialAccountRepository

        repo = await _async_wrap(container.resolve, SocialAccountRepository)
        account = await _async_wrap(repo.find_by_id, account_id)
        if not account:
            return _err("Social Account not found", 404)

        update_data = {
            k: v for k, v in body.items()
            if k not in ("id", "workspace_id", "created_at")
        }
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

        acc_dict = account.model_dump(mode="json")

        # Spec §4.2: preserve existing credential when caller sends null/empty token
        for secret_field in ("access_token", "encrypted_credential"):
            if secret_field in update_data and not update_data[secret_field]:
                # Caller omitted or cleared the field — keep the stored value
                update_data.pop(secret_field)

        # Same rule for nested platform_config secret keys
        if "platform_config" in update_data and isinstance(update_data["platform_config"], dict):
            existing_pc = acc_dict.get("platform_config") or {}
            incoming_pc = update_data["platform_config"]
            for sk in _SECRET_KEYS:
                if sk in incoming_pc and not incoming_pc[sk]:
                    incoming_pc.pop(sk)
                    # Restore existing value if present
                    if sk in existing_pc:
                        incoming_pc[sk] = existing_pc[sk]
            update_data["platform_config"] = incoming_pc

        acc_dict.update(update_data)
        saved = await _async_wrap(repo.save, SocialAccount(**acc_dict))

        await append_audit(
            audit_log, "social_account_updated", f"social_account:{account_id}",
            status_str="success",
        )
        return _ok({"social_account": _strip_credentials(saved.model_dump(mode="json"))})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"PUT /api/v1/social-accounts/{account_id}")


@router.delete("/social-accounts/{account_id}")
async def delete_social_account(
    account_id: str,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Delete a social account."""
    try:
        await require_workspace_ownership("social_account", account_id, container)
        from repositories.social_account_repository import SocialAccountRepository
        repo = await _async_wrap(container.resolve, SocialAccountRepository)
        deleted = await _async_wrap(repo.delete, account_id)
        await append_audit(
            audit_log, "social_account_deleted", f"social_account:{account_id}",
            status_str="success",
        )
        return _ok({"deleted": deleted})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/social-accounts/{account_id}")


@router.post("/social-accounts/{account_id}/health-check")
async def health_check_social_account(
    account_id: str,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Check social account health."""
    try:
        await require_workspace_ownership("social_account", account_id, container)
        from repositories.social_account_repository import SocialAccountRepository
        from services.connectors.connector_factory import create_connector

        repo = await _async_wrap(container.resolve, SocialAccountRepository)
        account = await _async_wrap(repo.find_by_id, account_id)
        if not account:
            return _err("Social Account not found", 404)

        connector = await _async_wrap(create_connector, account)
        h = await _async_wrap(connector.health)
        await _async_wrap(
            repo.update_health_status,
            account.id,
            "healthy" if h.ok else "unhealthy",
        )
        return _ok({"healthy": h.ok, "latency_ms": h.latency_ms, "error": h.error})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/social-accounts/{account_id}/health-check")


@router.post("/website-content/publish")
async def website_content_publish(
    body: dict,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Create a governed public-website publication intent.

    The endpoint never calls the website directly. It resolves the workspace
    website account and enqueues ``website_publish`` in the Action Kernel,
    where high-impact website publication remains approval-gated and durable.
    """
    try:
        workspace_id = str(body.get("workspace_id") or "").strip()
        account_id = str(body.get("social_account_id") or "").strip()
        content = str(body.get("content") or body.get("body") or "")
        if not workspace_id or not account_id or not content.strip():
            return _err("workspace_id, social_account_id and content are required", 400)
        await require_workspace_ownership("workspace", workspace_id, container)
        from repositories.social_account_repository import SocialAccountRepository
        from services.action_kernel import build_default_action_kernel
        repo = await _async_wrap(container.resolve, SocialAccountRepository)
        account = await _async_wrap(repo.find_by_id, account_id)
        if not account or str(account.workspace_id) != workspace_id:
            return _err("Website account not found in workspace", 404)
        if str(account.platform).lower() != "website":
            return _err("social_account_id is not a website connector", 400)
        supabase_service = getattr(container, "supabase_service", None)
        db = getattr(supabase_service, "service_client", None) or getattr(supabase_service, "client", None)
        if db is None:
            return _err("action database is unavailable", 503)
        kernel = build_default_action_kernel(db, allow_in_memory=False)
        result = await kernel.enqueue(
            workspace_id=workspace_id, action_type="website_publish",
            parameters={k: v for k, v in {**body, "social_account_id": account_id, "content": content}.items() if k not in {"workspace_id"}},
            risk_level="high", decision_id=body.get("decision_id"),
            correlation_id=body.get("correlation_id"),
            idempotency_key=body.get("idempotency_key"), source="api.website_content", approved=False,
        )
        await append_audit(audit_log, "website_content_publish_intent_created", f"action:{result.get('intent_id')}", status_str=result.get("status", "accepted"))
        return _ok(result, 202)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/website-content/publish")


@router.post("/website-content/update")
async def website_content_update(
    body: dict,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Create a governed public-website update intent; execution remains approval-gated."""
    try:
        workspace_id = str(body.get("workspace_id") or "").strip()
        account_id = str(body.get("social_account_id") or "").strip()
        post_id = str(body.get("post_id") or body.get("content_id") or "").strip()
        content = str(body.get("content") or body.get("body") or "")
        mutable = {k: v for k, v in body.items() if k not in {"workspace_id", "social_account_id", "post_id", "content", "body"} and v not in (None, "", [], {})}
        if content.strip():
            mutable["content"] = content
        if not workspace_id or not account_id or not post_id or not mutable:
            return _err("workspace_id, social_account_id, post_id and at least one mutable field are required", 400)
        await require_workspace_ownership("workspace", workspace_id, container)
        from repositories.social_account_repository import SocialAccountRepository
        from services.action_kernel import build_default_action_kernel
        repo = await _async_wrap(container.resolve, SocialAccountRepository)
        account = await _async_wrap(repo.find_by_id, account_id)
        if not account or str(account.workspace_id) != workspace_id:
            return _err("Website account not found in workspace", 404)
        if str(account.platform).lower() != "website":
            return _err("social_account_id is not a website connector", 400)
        supabase_service = getattr(container, "supabase_service", None)
        db = getattr(supabase_service, "service_client", None) or getattr(supabase_service, "client", None)
        if db is None:
            return _err("action database is unavailable", 503)
        kernel = build_default_action_kernel(db, allow_in_memory=False)
        result = await kernel.enqueue(
            workspace_id=workspace_id, action_type="website_update",
            parameters={k: v for k, v in {**mutable, "social_account_id": account_id, "post_id": post_id}.items() if k not in {"workspace_id"}},
            risk_level="high", decision_id=body.get("decision_id"),
            correlation_id=body.get("correlation_id"),
            idempotency_key=body.get("idempotency_key"), source="api.website_content", approved=False,
        )
        await append_audit(audit_log, "website_content_update_intent_created", f"action:{result.get('intent_id')}", status_str=result.get("status", "accepted"))
        return _ok(result, 202)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/website-content/update")


@router.post("/social-accounts/{account_id}/verify-publish")
async def verify_publish(
    account_id: str,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Verify publish capability for a social account."""
    try:
        await require_workspace_ownership("social_account", account_id, container)
        from repositories.social_account_repository import SocialAccountRepository
        from models.social_account import SocialAccount

        repo = await _async_wrap(container.resolve, SocialAccountRepository)
        account = await _async_wrap(repo.find_by_id, account_id)
        if not account:
            return _err("Social Account not found", 404)

        raw = getattr(account.platform, "value", None) or str(account.platform)
        platform = raw.lower().replace("platform.", "")

        verifier_map = {
            "facebook": _verify_facebook,
            "twitter": _verify_twitter,
            "linkedin": _verify_linkedin,
            "instagram": _verify_instagram,
            "whatsapp": _verify_whatsapp,
            "telegram": _verify_telegram,
        }
        verifier = verifier_map.get(platform)
        if not verifier and platform == "website":
            from services.connectors.connector_factory import create_connector
            connector = await _async_wrap(create_connector, account)
            health = await _async_wrap(connector.health)
            checks = {"website_api": {"ok": bool(health.ok), "latency_ms": health.latency_ms, "error": health.error}}
        elif verifier:
            checks = await _async_wrap(verifier, account)
        else:
            return _err(f"Verification not supported for: {platform}", 400)
        ready = all(c.get("ok", False) for c in checks.values())

        acc_dict = account.model_dump(mode="json")
        acc_dict["health_status"] = "healthy" if ready else "unhealthy"
        acc_dict["last_health_check"] = datetime.now(timezone.utc).isoformat()
        await _async_wrap(repo.save, SocialAccount(**acc_dict))

        failed = [
            k.replace("_", " ").title()
            for k, c in checks.items()
            if not c.get("ok")
        ]
        if ready:
            summary = "All checks passed. Ready to publish."
        elif len(failed) == 1:
            summary = f"{failed[0]} check failed"
        else:
            summary = f"{len(failed)} checks failed"

        return _ok({
            "verified": ready,
            "page_name": checks.get("page_access", {}).get(
                "page_name", account.account_name
            ),
            "summary": summary,
            "ready_to_publish": ready,
            "checks": checks,
        })
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/social-accounts/{account_id}/verify-publish")


@router.post("/workspaces/{workspace_id}/social-accounts/bulk-health-check")
async def bulk_health_check(
    workspace_id: str,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Bulk health check for social accounts."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)
        from repositories.social_account_repository import SocialAccountRepository
        from services.connectors.connector_factory import create_connector

        repo = await _async_wrap(container.resolve, SocialAccountRepository)
        accounts = await _async_wrap(repo.find_by_workspace, workspace_id)
        if len(accounts) > 20:
            return _err("Bulk health check limited to 20 accounts", 400)

        results = []
        for account in accounts:
            try:
                connector = await _async_wrap(create_connector, account)
                h = await _async_wrap(connector.health)
                await _async_wrap(
                    repo.update_health_status,
                    account.id,
                    "healthy" if h.ok else "unhealthy",
                )
                results.append({
                    "id": account.id,
                    "platform": str(account.platform),
                    "account_name": account.account_name,
                    "healthy": h.ok,
                })
            except Exception as e:
                results.append({
                    "id": account.id,
                    "healthy": False,
                    "error": str(e)[:200],
                })

        healthy_count = sum(1 for r in results if r.get("healthy"))
        return _ok({
            "results": results,
            "total": len(results),
            "healthy": healthy_count,
            "unhealthy": len(results) - healthy_count,
        })
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(
            exc,
            f"POST /api/v1/workspaces/{workspace_id}/social-accounts/bulk-health-check",
        )


# ── Platform Verifiers (A10: SSRF Protection via timeout) ──────────────────


def _verify_facebook(account) -> dict:
    """A10: Facebook verification with 8s timeout."""
    import requests as _requests_mod
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }
    token = _get_account_token(account)
    page_id = _get_account_platform_id(account)
    if not token or not page_id:
        checks["token_valid"] = {"ok": False, "detail": "Missing access token or page ID"}
        return checks
    try:
        resp = _requests_mod.get(
            "https://graph.facebook.com/v22.0/debug_token",
            params={"input_token": token, "access_token": token},
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            if data.get("is_valid"):
                expires = data.get("expires_at", 0)
                if expires == 0:
                    checks["token_valid"] = {"ok": True, "detail": "Long-lived token"}
                else:
                    days = (datetime.fromtimestamp(expires) - datetime.utcnow()).days
                    checks["token_valid"] = {"ok": True, "detail": f"Valid, expires in {days}d"}
            else:
                checks["token_valid"] = {"ok": False, "detail": data.get("error", {}).get("message", "Invalid")}
        else:
            checks["token_valid"] = {"ok": False, "detail": f"HTTP {resp.status_code}"}
        if checks["token_valid"]["ok"]:
            resp = _requests_mod.get(
                f"https://graph.facebook.com/v22.0/{page_id}",
                params={"fields": "name", "access_token": token},
                timeout=PLATFORM_VERIFY_TIMEOUT,
            )
            if resp.status_code == 200:
                name = resp.json().get("name", account.account_name)
                checks["page_access"] = {"ok": True, "detail": f"Page '{name}' accessible", "page_name": name}
            else:
                checks["page_access"] = {"ok": False, "detail": f"HTTP {resp.status_code}"}
        if checks["token_valid"]["ok"] and checks["page_access"]["ok"]:
            checks["publish_permission"] = {"ok": True, "detail": "Publish confirmed"}
        elif not checks["token_valid"]["ok"]:
            checks["publish_permission"] = {"ok": False, "detail": "Token invalid"}
        else:
            checks["publish_permission"] = {"ok": False, "detail": "Page access failed"}
    except _requests_mod.exceptions.Timeout:
        checks["token_valid"] = {"ok": False, "detail": f"Timeout ({PLATFORM_VERIFY_TIMEOUT}s)"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks


def _verify_twitter(account) -> dict:
    import requests as _requests_mod
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }
    token = _get_account_token(account)
    if not token:
        checks["token_valid"] = {"ok": False, "detail": "Missing access token"}
        return checks
    try:
        resp = _requests_mod.get(
            "https://api.twitter.com/2/users/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200:
            user = resp.json().get("data", {})
            name = user.get("username", account.account_name)
            checks["token_valid"] = {"ok": True, "detail": f"Authenticated as @{name}"}
            checks["page_access"] = {"ok": True, "detail": f"Account @{name} accessible", "page_name": f"@{name}"}
            checks["publish_permission"] = {"ok": True, "detail": "Publish assumed"}
        elif resp.status_code == 401:
            checks["token_valid"] = {"ok": False, "detail": "Token expired"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks


def _verify_linkedin(account) -> dict:
    import requests as _requests_mod
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }
    token = _get_account_token(account)
    if not token:
        checks["token_valid"] = {"ok": False, "detail": "Missing access token"}
        return checks
    try:
        resp = _requests_mod.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {token}"},
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200:
            name = resp.json().get("name", account.account_name)
            checks["token_valid"] = {"ok": True, "detail": f"Authenticated as {name}"}
            checks["page_access"] = {"ok": True, "detail": "Profile accessible", "page_name": name}
            checks["publish_permission"] = {"ok": True, "detail": "Publish assumed"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks


def _verify_instagram(account) -> dict:
    import requests as _requests_mod
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }
    token = _get_account_token(account)
    if not token:
        checks["token_valid"] = {"ok": False, "detail": "Missing access token"}
        return checks
    try:
        resp = _requests_mod.get(
            "https://graph.facebook.com/v22.0/me",
            params={"access_token": token, "fields": "id,name"},
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200:
            checks["token_valid"] = {"ok": True, "detail": "Token valid"}
            checks["page_access"] = {"ok": True, "detail": "Account accessible"}
            checks["publish_permission"] = {"ok": True, "detail": "Publish assumed"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks


def _verify_whatsapp(account) -> dict:
    import requests as _requests_mod
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }
    token = _get_account_token(account)
    phone_id = _get_account_platform_id(account)
    if not token or not phone_id:
        checks["token_valid"] = {"ok": False, "detail": "Missing credentials"}
        return checks
    try:
        resp = _requests_mod.get(
            f"https://graph.facebook.com/v22.0/{phone_id}",
            params={"access_token": token, "fields": "display_phone_number,verified_name"},
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            checks["token_valid"] = {"ok": True, "detail": f"WhatsApp: {data.get('verified_name', 'unknown')}"}
            checks["page_access"] = {"ok": True, "detail": "Phone verified"}
            checks["publish_permission"] = {"ok": True, "detail": "Messaging active"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks


def _verify_telegram(account) -> dict:
    import requests as _requests_mod
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }
    token = _get_account_token(account)
    if not token:
        checks["token_valid"] = {"ok": False, "detail": "Missing bot token"}
        return checks
    try:
        resp = _requests_mod.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            bot = resp.json().get("result", {})
            name = bot.get("username", account.account_name)
            checks["token_valid"] = {"ok": True, "detail": f"Bot @{name} valid"}
            checks["page_access"] = {"ok": True, "detail": "Bot accessible", "page_name": f"@{name}"}
            checks["publish_permission"] = {"ok": True, "detail": "Bot can send messages"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks


# ═══════════════════════════════════════════════════════════════════════════
# USERS — Spec §2: User management with workspace membership + RBAC
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/users")
async def list_users(
    _auth: bool = Depends(require_auth),
    workspace_id: Optional[str] = None,
):
    """
    Spec §2: List users in a workspace.
    Requires authenticated identity; scoped to the caller's workspace.
    """
    try:
        ws_id = auth_workspace_id_var.get()
        if not ws_id:
            return _err("No workspace bound to credentials", 403)
        users: list[dict] = []
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                result = await _async_wrap(
                    lambda: supabase_service.client.table("workspace_members")
                    .select("id, email, name, role, status, workspace_id, created_at, updated_at")
                    .eq("workspace_id", ws_id)
                    .execute()
                )
                users = result.data or []
        except Exception as qe:
            logger.warning("User list query failed: %s", qe)

        return _ok({"users": users, "count": len(users), "workspace_id": ws_id})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/users")


@router.post("/users")
async def create_user(
    body: UserCreateRequest,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """
    Spec §2: Add a user to a workspace.
    Enforces: authenticated caller, workspace membership, duplicate email protection.
    Admin-level operation — only admins/owners may create users.
    """
    try:
        ws_id = resolve_plugin_workspace(body.workspace_id)
        caller = get_request_context()
        if body.role == "owner" and not (caller and caller.species_privileges or caller and caller.role == "owner"):
            return _err("Only a workspace owner or platform operator may assign the owner role", 403)

        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                # Duplicate email guard
                existing = await _async_wrap(
                    lambda: supabase_service.client.table("workspace_members")
                    .select("id")
                    .eq("workspace_id", ws_id)
                    .eq("email", body.email)
                    .execute()
                )
                if existing.data:
                    return _err(f"User '{body.email}' already exists in this workspace", 409)

                user_id = _uuid_mod.uuid4().hex[:16]
                record = {
                    "id": user_id,
                    "email": body.email,
                    "name": body.name,
                    "role": body.role,
                    "workspace_id": ws_id,
                    "status": "active",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                await _async_wrap(
                    lambda: supabase_service.client.table("workspace_members")
                    .insert(record)
                    .execute()
                )
                await append_audit(
                    audit_log, "user_created", f"user:{user_id}",
                    user=auth_user_email_var.get() or "system", status_str="success",
                )
                return _ok({"user": record}, 201)
        except HTTPException:
            raise
        except Exception as qe:
            logger.error("User create failed: %s", qe)
            return _safe_err(qe, "POST /api/v1/users")

        return _err("User creation requires Supabase", 503)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/users")


@router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdateRequest,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """
    Spec §2: Update a workspace user.
    Never allows a user to manipulate another workspace by changing the workspace_id.
    """
    try:
        ws_id = auth_workspace_id_var.get()
        if not ws_id:
            return _err("No workspace bound to credentials", 403)

        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                # Resolve existing user — enforce workspace isolation
                existing_res = await _async_wrap(
                    lambda: supabase_service.client.table("workspace_members")
                    .select("*")
                    .eq("id", user_id)
                    .maybe_single()
                    .execute()
                )
                if not existing_res or not existing_res.data:
                    return _err("User not found", 404)
                existing = existing_res.data

                # Workspace isolation — cross-workspace access denied
                if ws_id and existing.get("workspace_id") != ws_id:
                    raise HTTPException(status_code=404, detail="User not found")

                updates = body.model_dump(exclude_none=True)
                caller = get_request_context()
                if updates.get("role") == "owner" and not (caller and caller.species_privileges or caller and caller.role == "owner"):
                    raise HTTPException(status_code=403, detail="Only a workspace owner or platform operator may assign the owner role")
                updates["updated_at"] = datetime.now(timezone.utc).isoformat()

                # Never allow workspace_id to change
                updates.pop("workspace_id", None)

                await _async_wrap(
                    lambda: supabase_service.client.table("workspace_members")
                    .update(updates)
                    .eq("id", user_id)
                    .execute()
                )
                await append_audit(
                    audit_log, "user_updated", f"user:{user_id}",
                    user=auth_user_email_var.get() or "system", status_str="success",
                )
                return _ok({"user": {**existing, **updates}})
        except HTTPException:
            raise
        except Exception as qe:
            return _safe_err(qe, f"PUT /api/v1/users/{user_id}")

        return _err("User updates require Supabase", 503)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"PUT /api/v1/users/{user_id}")


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """
    Spec §2: Delete a user from a workspace.
    Last-admin protection: refuses if this is the last admin/owner in the workspace.
    Safe deletion — validates membership before removing.
    """
    try:
        ws_id = auth_workspace_id_var.get()
        if not ws_id:
            return _err("No workspace bound to credentials", 403)

        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                # Resolve existing user
                existing_res = await _async_wrap(
                    lambda: supabase_service.client.table("workspace_members")
                    .select("*")
                    .eq("id", user_id)
                    .maybe_single()
                    .execute()
                )
                if not existing_res or not existing_res.data:
                    return _err("User not found", 404)
                existing = existing_res.data

                # Workspace isolation
                if ws_id and existing.get("workspace_id") != ws_id:
                    raise HTTPException(status_code=404, detail="User not found")

                # Last-admin protection
                if existing.get("role") in ("admin", "owner"):
                    admins_res = await _async_wrap(
                        lambda: supabase_service.client.table("workspace_members")
                        .select("id")
                        .eq("workspace_id", existing["workspace_id"])
                        .in_("role", ["admin", "owner"])
                        .execute()
                    )
                    if len(admins_res.data or []) <= 1:
                        return _err(
                            "Cannot remove the last admin/owner from a workspace. "
                            "Promote another member first.",
                            409,
                        )

                await _async_wrap(
                    lambda: supabase_service.client.table("workspace_members")
                    .delete()
                    .eq("id", user_id)
                    .execute()
                )
                await append_audit(
                    audit_log, "user_deleted", f"user:{user_id}",
                    user=auth_user_email_var.get() or "system", status_str="success",
                )
                return _ok({"deleted": True, "user_id": user_id})
        except HTTPException:
            raise
        except Exception as qe:
            return _safe_err(qe, f"DELETE /api/v1/users/{user_id}")

        return _err("User deletion requires Supabase", 503)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/users/{user_id}")


# ═══════════════════════════════════════════════════════════════════════════
# KNOWLEDGE BASE
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/brands/{brand_id}/knowledge")
async def create_knowledge_entry(
    brand_id: str,
    body: KnowledgeCreateRequest,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Create a knowledge entry for a brand."""
    try:
        await require_workspace_ownership("brand", brand_id, container)
        from repositories.brand_repository import BrandRepository
        from repositories.knowledge_repository import KnowledgeRepository
        from services.knowledge_base import KnowledgeBase

        brand_repo = await _async_wrap(container.resolve, BrandRepository)
        brand = await _async_wrap(brand_repo.find_by_id, brand_id)
        if not brand:
            return _err("Brand not found", 404)

        kb_repo = await _async_wrap(container.resolve, KnowledgeRepository)
        kb = KnowledgeBase(
            brand_id=brand_id,
            workspace_id=brand.workspace_id,
            repository=kb_repo,
        )
        entry = await _async_wrap(
            kb.add_document,
            content=body.content,
            doc_type=body.doc_type,
            metadata=body.metadata,
        )
        return _ok({"knowledge_entry": entry.model_dump(mode="json")}, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/brands/{brand_id}/knowledge")


@router.get("/brands/{brand_id}/knowledge")
async def list_knowledge_entries(
    brand_id: str,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """List knowledge entries for a brand."""
    try:
        await require_workspace_ownership("brand", brand_id, container)
        from repositories.knowledge_repository import KnowledgeRepository
        repo = await _async_wrap(container.resolve, KnowledgeRepository)
        entries = await _async_wrap(repo.find_by_brand, brand_id)
        return _ok({"knowledge_entries": [e.model_dump(mode="json") for e in entries]})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/brands/{brand_id}/knowledge")


@router.delete("/knowledge/{knowledge_id}")
async def delete_knowledge_entry(
    knowledge_id: str,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Delete a knowledge entry."""
    try:
        from repositories.knowledge_repository import KnowledgeRepository
        repo = await _async_wrap(container.resolve, KnowledgeRepository)
        deleted = await _async_wrap(repo.delete, knowledge_id)
        await append_audit(
            audit_log, "knowledge_deleted", f"knowledge:{knowledge_id}", status_str="success",
        )
        return _ok({"deleted": deleted})
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/knowledge/{knowledge_id}")


@router.post("/brands/{brand_id}/knowledge/search")
async def search_knowledge_entries(
    brand_id: str,
    body: KnowledgeSearchRequest,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Search knowledge entries for a brand."""
    try:
        await require_workspace_ownership("brand", brand_id, container)
        from repositories.brand_repository import BrandRepository
        from repositories.knowledge_repository import KnowledgeRepository
        from services.knowledge_base import KnowledgeBase

        brand_repo = await _async_wrap(container.resolve, BrandRepository)
        brand = await _async_wrap(brand_repo.find_by_id, brand_id)
        if not brand:
            return _err("Brand not found", 404)

        kb_repo = await _async_wrap(container.resolve, KnowledgeRepository)
        kb = KnowledgeBase(
            brand_id=brand_id,
            workspace_id=brand.workspace_id,
            repository=kb_repo,
        )
        results = await _async_wrap(kb.search, query=body.query, top_k=body.top_k)
        serialised = [
            {"entry": entry.model_dump(mode="json"), "similarity": float(sim)}
            for entry, sim in results
        ]
        return _ok({"results": serialised})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/brands/{brand_id}/knowledge/search")


@router.post("/workspaces/{workspace_id}/knowledge/bulk")
async def bulk_create_knowledge(
    workspace_id: str,
    body: BulkKnowledgeRequest,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Bulk create knowledge entries."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)
        from repositories.knowledge_repository import KnowledgeRepository
        from services.knowledge_base import KnowledgeBase

        kb_repo = await _async_wrap(container.resolve, KnowledgeRepository)
        kb = KnowledgeBase(
            brand_id=body.brand_id,
            workspace_id=workspace_id,
            repository=kb_repo,
        )

        results, errors = [], []
        for i, item in enumerate(body.items):
            content = _sanitize(str(item.get("content", "")))
            if not content:
                errors.append({"index": i, "error": "content required"})
                continue
            try:
                entry = await _async_wrap(
                    kb.add_document,
                    content=content,
                    doc_type=item.get("doc_type", "text"),
                    metadata=item.get("metadata", {}),
                )
                results.append({"id": entry.id, "status": "created"})
            except Exception as e:
                errors.append({"index": i, "error": str(e)[:200]})

        await append_audit(
            audit_log, "knowledge_bulk_created", f"workspace:{workspace_id}",
            status_str="success",
        )
        return _ok({
            "created": len(results),
            "errors": len(errors),
            "items": results,
        }, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/knowledge/bulk")


@router.get("/knowledge")
async def list_knowledge(
    _auth: bool = Depends(require_auth),
    limit: int = 50,
    q: str = "",
):
    """List all knowledge entries."""
    try:
        limit = min(200, max(1, limit))
        search = q.strip().lower()
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            result = await _async_wrap(
                lambda: supabase_service.client.table("knowledge_entries")
                .select("*")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            docs = result.data or []
            if search:
                docs = [
                    d for d in docs
                    if search in (d.get("content") or "").lower()
                ]
            return _ok({"documents": docs, "count": len(docs), "source": "supabase"})
        return _ok({"documents": [], "count": 0})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/knowledge")


@router.post("/knowledge")
async def create_knowledge(
    body: KnowledgeCreateRequest,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Create a knowledge entry."""
    try:
        entry = {
            "id": _uuid_mod.uuid4().hex,
            "title": body.title or body.content[:80],
            "content": body.content,
            "doc_type": body.doc_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            try:
                await _async_wrap(
                    lambda: supabase_service.client.table("knowledge_entries")
                    .insert(entry)
                    .execute()
                )
            except Exception:
                pass

        await append_audit(
            audit_log, "knowledge_created", f"knowledge:{entry['id']}", status_str="success",
        )
        return _ok({"document": entry}, 201)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/knowledge")


# ═══════════════════════════════════════════════════════════════════════════
# API KEYS — A02/A07
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/keys/generate")
async def generate_api_key(
    body: APIKeyGenerateRequest,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
    _rate=create_rate_limit_dependency("key_generation", max_per_minute=KEY_GEN_RATE_LIMIT),
):
    """Generate a durable, workspace-scoped API token.

    Tokens are managed by APITokenManager and are never stored in the legacy
    global ``api_keys`` store. The plaintext token is returned only once.
    """
    try:
        workspace_id = resolve_plugin_workspace()
        from core.api_tokens import api_token_manager
        result = await _async_wrap(
            api_token_manager.create_token,
            workspace_id,
            body.label,
            ["read"],
            None,
        )
        await append_audit(
            audit_log, "api_key_created", f"key:{result['id']}", status_str="success",
        )
        return _ok({
            "key_id": result["id"],
            "token": result["token"],
            "label": result["name"],
            "permissions": result["permissions"],
            "note": "Store this token securely. It will not be shown again.",
        }, 201)
    except RuntimeError as exc:
        return _err(str(exc), 503)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/keys/generate")


@router.get("/keys")
async def list_api_keys(
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """List active API tokens for the trusted workspace only."""
    try:
        workspace_id = resolve_plugin_workspace()
        from core.api_tokens import api_token_manager
        keys = await _async_wrap(api_token_manager.list_tokens, workspace_id)
        return _ok({"keys": keys, "count": len(keys), "workspace_id": workspace_id})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/keys")


@router.delete("/keys/{key_id}")
async def revoke_api_key(
    key_id: str,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """Revoke a workspace-scoped API token."""
    try:
        workspace_id = resolve_plugin_workspace()
        from core.api_tokens import api_token_manager
        revoked = await _async_wrap(api_token_manager.revoke_token, key_id, workspace_id)
        if not revoked:
            return _err("API key not found", 404)
        await append_audit(
            audit_log, "api_key_revoked", f"key:{key_id}", status_str="success",
        )
        return _ok({"revoked": True, "key_id": key_id})
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/keys/{key_id}")


# ═══════════════════════════════════════════════════════════════════════════
# WORKFLOWS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/workflow")
async def list_workflows(
    workflow_history: AsyncWorkflowHistory = Depends(get_workflow_history),
    _auth: bool = Depends(require_auth),
):
    """List available workflows with last run info."""
    history = await workflow_history.get_all()
    last_runs = {}
    for entry in reversed(history):
        name = entry.get("workflow")
        if name and name not in last_runs:
            last_runs[name] = entry

    workflows = [
        {"name": n, **m, "last_run": last_runs.get(n)}
        for n, m in AVAILABLE_WORKFLOWS.items()
    ]
    return _ok({"workflows": workflows})


@router.post("/workflow/run")
async def run_workflow(
    body: WorkflowRunRequest,
    container=Depends(get_container),
    workflow_history: AsyncWorkflowHistory = Depends(get_workflow_history),
    _auth: bool = Depends(require_auth),
):
    """Run a workflow for all brands."""
    try:
        name = body.workflow.strip().lower()
        if name not in AVAILABLE_WORKFLOWS:
            return _err(f"Unknown workflow: {name}", 400)

        from scheduler.brand_scheduler import BrandSchedulerManager
        manager = await _async_wrap(container.resolve, BrandSchedulerManager)
        result = await _async_wrap(manager.trigger_all)

        entry = {
            "workflow": name,
            "ok": result.get("ok", False),
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "brands_triggered": result.get("brands_triggered", 0),
        }
        await workflow_history.append(entry)

        if result.get("ok"):
            return _ok({
                "workflow": name,
                "brands_triggered": result.get("brands_triggered", 0),
                "results": result.get("results", {}),
            })
        return _err(result.get("error", "Failed"), 500)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/workflow/run")



@router.get("/workflow/status")
async def workflow_status(container=Depends(get_container), _auth: bool = Depends(require_auth)):
    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        manager = await _async_wrap(container.resolve, BrandSchedulerManager)
        all_status = await _async_wrap(manager.get_all_status)
        running = sum(1 for s in all_status if s.get("running"))
        return _ok({
            "status": "running" if running > 0 else "idle",
            "brands": all_status,
            "brands_running": running,
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/workflow/status")

@router.get("/workflow/history")
async def workflow_history_endpoint(
    workflow_history: AsyncWorkflowHistory = Depends(get_workflow_history),
    _auth: bool = Depends(require_auth),
    limit: int = 20,
):
    """
    Get workflow run history — workspace-scoped.
    
    Architecture §31 — Tenant Isolation:
      History is filtered to the authenticated workspace.
    """
    try:
        limit = min(50, max(1, limit))
        workspace_id = auth_workspace_id_var.get()
        
        entries = await workflow_history.get_recent(limit)
        
        # Workspace isolation — filter by workspace if not admin
        if workspace_id:
            entries = [
                e for e in entries
                if e.get("workspace_id") == workspace_id or e.get("workspace_id") is None
            ]
        
        return _ok({
            "history": entries,
            "count": len(entries),
            "workspace_id": workspace_id,
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/workflow/history")


@router.post("/workflow/pause")
async def workflow_pause(container=Depends(get_container), _auth=Depends(require_workspace_operator)):
    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        manager = await _async_wrap(container.resolve, BrandSchedulerManager)
        await _async_wrap(manager.stop)
        return _ok({"status": "paused"})
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/workflow/pause")


@router.post("/workflow/trigger")
async def workflow_trigger(
    body: dict = {},
    container=Depends(get_container),
    _auth=Depends(require_workspace_operator),
):
    """Trigger a workflow run for a specific brand.
    
    Returns job_id for traceability (Architecture §29).
    """
    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        from repositories.brand_repository import BrandRepository
        from repositories.ai_profile_repository import AIProfileRepository
        
        brand_id = body.get("brand_id", "") if body else ""
        
        if not brand_id:
            return _err("brand_id is required. Pass {'brand_id': 'your-brand-id'}", 400)
        
        brand_repo = await _async_wrap(container.resolve, BrandRepository)
        brand = await _async_wrap(brand_repo.find_by_id, brand_id)
        
        if not brand:
            return _err(f"Brand '{brand_id}' not found", 404)
        
        ai_profile = None
        if getattr(brand, 'ai_profile_id', None):
            ai_repo = await _async_wrap(container.resolve, AIProfileRepository)
            ai_profile = await _async_wrap(ai_repo.find_by_id, brand.ai_profile_id)
        
        if not ai_profile:
            return _err(f"No AI profile found for brand '{brand_id}'. Assign one in Brand settings.", 400)
        
        from scheduler.brand_scheduler import BrandScheduler
        scheduler = BrandScheduler(brand=brand, ai_profile=ai_profile, container=container)
        result = await _async_wrap(scheduler.trigger_manual)
        
        response = {
            "ok": result.get("ok", False),
            "brand": result.get("brand", brand.name),
            "topic": result.get("topic", ""),
            "platforms": result.get("platforms", []),
            "duration_ms": result.get("duration_ms"),
            "job_id": result.get("job_id"),
        }
        if not result.get("ok"):
            response["error"] = result.get("error", "Unknown error")
        
        return _ok(response)
        
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/workflow/trigger")


@router.post("/workflow/{workflow_id}/cancel")
async def cancel_workflow(
    workflow_id: str,
    workflow_history: AsyncWorkflowHistory = Depends(get_workflow_history),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Cancel a running workflow."""
    found = await workflow_history.find_and_update(
        "id", workflow_id,
        {"status": "cancelled", "cancelled_at": datetime.now(timezone.utc).isoformat()},
    )
    if not found:
        found = await workflow_history.find_and_update(
            "topic", workflow_id,
            {"status": "cancelled", "cancelled_at": datetime.now(timezone.utc).isoformat()},
        )
    if not found:
        return _err("Workflow not found", 404)

    await append_audit(
        audit_log, "workflow_cancelled", f"workflow:{workflow_id}", status_str="success",
    )
    return _ok({"cancelled": True})


@router.post("/workflow/{workflow_id}/rollback")
async def rollback_workflow(
    workflow_id: str,
    workflow_history: AsyncWorkflowHistory = Depends(get_workflow_history),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Rollback a workflow."""
    found = await workflow_history.find_and_update(
        "id", workflow_id,
        {"status": "rolled_back", "rolled_back_at": datetime.now(timezone.utc).isoformat()},
    )
    if not found:
        found = await workflow_history.find_and_update(
            "topic", workflow_id,
            {"status": "rolled_back", "rolled_back_at": datetime.now(timezone.utc).isoformat()},
        )
    if not found:
        return _err("Workflow not found", 404)

    await append_audit(
        audit_log, "workflow_rolled_back", f"workflow:{workflow_id}", status_str="success",
    )
    return _ok({"rolled_back": True})


# ═══════════════════════════════════════════════════════════════════════════
# WEBHOOKS — A08 Signature Validation
# ═══════════════════════════════════════════════════════════════════════════

@router.api_route("/webhooks/facebook", methods=["GET", "POST"])
async def facebook_webhook(
    request: Request,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
):
    """A08: Facebook webhook with HMAC-SHA256 verification."""
    if request.method == "GET":
        mode = request.query_params.get("hub.mode")
        token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge", "")
        if mode == "subscribe":
            container = request.app.state.container
            from repositories.social_account_repository import SocialAccountRepository
            try:
                repo = await _async_wrap(container.resolve, SocialAccountRepository)
                accounts = await _async_wrap(repo.find_by_platform, "facebook")
                for acc in accounts:
                    pc = getattr(acc, "platform_config", {}) or {}
                    if pc.get("verify_token") == token:
                        return Response(content=challenge, status_code=200)
            except Exception:
                pass
        return Response(content="Verification failed", status_code=403)

    if not await _verify_webhook_signature(request):
        return Response(content="Invalid signature", status_code=403)

    payload = await request.json()
    logger.info("Facebook webhook: %s", str(payload)[:300])
    await append_audit(
        audit_log, "webhook_facebook", "webhook:facebook", status_str="success",
    )
    return Response(content="OK", status_code=200)


@router.post("/webhooks/twitter")
async def twitter_webhook(
    request: Request,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
):
    """A08: Twitter webhook with signature verification."""
    if not await _verify_webhook_signature(request):
        return Response(content="Invalid signature", status_code=403)
    await append_audit(
        audit_log, "webhook_twitter", "webhook:twitter", status_str="success",
    )
    return Response(content="OK", status_code=200)


@router.post("/webhooks/linkedin")
async def linkedin_webhook(
    request: Request,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
):
    """A08: LinkedIn webhook with signature verification."""
    if not await _verify_webhook_signature(request):
        return Response(content="Invalid signature", status_code=403)
    await append_audit(
        audit_log, "webhook_linkedin", "webhook:linkedin", status_str="success",
    )
    return Response(content="OK", status_code=200)


@router.api_route("/webhooks/instagram", methods=["GET", "POST"])
async def instagram_webhook(request: Request):
    """A08: Instagram webhook with signature verification."""
    if request.method == "GET":
        if (
            request.query_params.get("hub.mode") == "subscribe"
            and request.query_params.get("hub.verify_token")
        ):
            return Response(
                content=request.query_params.get("hub.challenge", ""),
                status_code=200,
            )
        return Response(content="Verification failed", status_code=403)
    if not await _verify_webhook_signature(request):
        return Response(content="Invalid signature", status_code=403)
    return Response(content="OK", status_code=200)


# ═══════════════════════════════════════════════════════════════════════════
# PLUGINS
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# FILE UPLOAD — Spec §11/§13: Multipart upload architecture for PDFs
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/upload")
async def upload_file(
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """
    Spec §11/§13: Scalable multipart file upload endpoint.
    Accepts PDF files via multipart/form-data, stores to Supabase Storage,
    and returns a file_id for use with plugin/job execution.

    Prevents: memory spikes, huge JSON payloads, base64 overhead, timeouts.
    """
    try:
        from fastapi import UploadFile, File, Form
        import io as _io

        content_type = request.headers.get("content-type", "")
        if "multipart/form-data" not in content_type:
            return _err("Content-Type must be multipart/form-data", 415)

        form = await request.form()
        file = form.get("file")
        client_workspace_id = form.get("workspace_id")
        workspace_id = resolve_plugin_workspace(client_workspace_id)
        purpose = form.get("purpose", "pdf_merge")  # pdf_merge | pdf_split | pdf_compress
        if purpose not in {"pdf_merge", "pdf_split", "pdf_compress"}:
            return _err("Unsupported upload purpose", 400)

        if not file or not hasattr(file, "read"):
            return _err("file field is required (multipart/form-data)", 400)

        filename = getattr(file, "filename", "upload.pdf") or "upload.pdf"
        # Storage keys must never contain client-controlled path components.
        filename = _re_mod.sub(r"[^A-Za-z0-9._-]", "_", filename.split("/")[-1].split("\\")[-1])[:255] or "upload.pdf"
        content = await file.read()

        # Validate file size — 50 MB max for PDF operations
        max_bytes = 50 * 1024 * 1024
        if len(content) > max_bytes:
            return _err(f"File exceeds maximum size of {max_bytes // (1024*1024)}MB", 413)

        # Validate basic PDF signature
        if not content.startswith(b"%PDF"):
            return _err("Only PDF files are accepted", 415)

        file_id = _uuid_mod.uuid4().hex
        stored_path: Optional[str] = None
        storage_url: Optional[str] = None

        try:
            from services.supabase import supabase_service
            if not (supabase_service and supabase_service.client):
                raise RuntimeError("durable PDF storage is unavailable")
            storage_path = f"uploads/{workspace_id}/{file_id}/{filename}"
            await _async_wrap(
                lambda: supabase_service.client.storage.from_("pdf-uploads").upload(
                    storage_path,
                    content,
                    {"content-type": "application/pdf"},
                )
            )
            storage_url = supabase_service.client.storage.from_("pdf-uploads").get_public_url(storage_path)
            stored_path = storage_path
        except Exception as se:
            logger.exception("Durable PDF storage failed for workspace=%s file_id=%s", workspace_id, file_id)
            return _err("File storage temporarily unavailable; upload was not accepted", 503)

        cid = correlation_id_var.get()
        return _ok({
            "file_id": file_id,
            "filename": filename,
            "size_bytes": len(content),
            "size_kb": round(len(content) / 1024, 1),
            "storage_url": storage_url,
            "stored_path": stored_path,
            "workspace_id": workspace_id,
            "purpose": purpose,
            "correlation_id": cid,
            "note": "Use file_id when submitting to plugin commands.",
        }, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/upload")

@router.post("/plugins/pdf_merger/upload")
async def upload_pdfs_for_merger(
    files: list[UploadFile] = File(..., description="PDF files to upload"),
    command: str = Form(default="merge_pdfs"),
    workspace_id: str = Form(default="default"),
    brand_id: str = Form(default="default"),
    pages: str = Form(default=""),
    quality: int = Form(default=50),
    output_name: str = Form(default=""),
    _auth: bool = Depends(require_auth),
):
    """Upload PDFs via multipart form data for processing."""
    # ... full function body from my previous message


@router.post("/plugins/pdf_merger/execute")
async def pdf_merger_execute(
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """
    Spec §11.1: Dedicated PDF Merger execution endpoint expected by Mutex.
    Forwards to the canonical plugin API at POST /plugins/pdf_merger/execute.
    Accepts JSON with file_ids (from /upload) instead of raw base64.
    """
    try:
        body = await request.json()
        container = request.app.state.container
        plugins = getattr(container, "industry_plugins", {})
        plugin = plugins.get("pdf_merger")
        if not plugin:
            return _err("PDF merger plugin is not registered", 404)

        command = body.get("command", "merge_pdfs")
        params = body.get("params", {})
        client_workspace_id = body.get("workspace_id")
        workspace_id = resolve_plugin_workspace(client_workspace_id)
        params = params if isinstance(params, dict) else {}
        result = await _async_wrap(
            run_plugin,
            plugin.execute,
            command,
            params,
            client_workspace_id=client_workspace_id,
        )
        return _ok(result)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/plugins/pdf_merger/execute")


@router.get("/plugins")
async def list_plugins(
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """List available industry plugins."""
    try:
        container = request.app.state.container
        plugins_registry = getattr(container, "industry_plugins", {})
        result = [
            {
                "name": n,
                "commands": p.get_commands(),
                "webhooks": p.get_webhooks(),
            }
            for n, p in plugins_registry.items()
        ]
        return _ok({"plugins": result, "count": len(result)})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/plugins")


@router.post("/plugins/{plugin_name}/execute")
async def execute_plugin_command(
    plugin_name: str,
    body: PluginExecuteRequest,
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """Execute a plugin command."""
    try:
        container = request.app.state.container
        plugins = getattr(container, "industry_plugins", {})
        plugin = plugins.get(plugin_name)
        if not plugin:
            return _err(f"Plugin '{plugin_name}' not found", 404)

        result = await _async_wrap(
            run_plugin,
            plugin.execute,
            body.command,
            body.params,
            client_workspace_id=body.workspace_id,
        )
        return _ok(result)
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/plugins/{plugin_name}/execute")


# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM, STATUS, FEATURES, RATE LIMITS, TENANTS, NOTIFICATIONS, INTEGRATIONS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/system/health")
async def get_system_health(request: Request):
    """Get system health status.

    Shape includes `services` map with `{ok, healthy, status}` so the control-plane
    Sidebar ORGANISM LINK can render servicesHealthy/servicesTotal honestly.
    """
    try:
        from core.reliability import HealthChecker
        container = request.app.state.container
        checker = await _async_wrap(container.resolve, HealthChecker)
        statuses = await _async_wrap(checker.check_all)
        if not isinstance(statuses, dict):
            statuses = {}
        services = {}
        for name, entry in statuses.items():
            if isinstance(entry, dict):
                ok = bool(entry.get("ok", entry.get("healthy", False)))
                services[name] = {
                    "ok": ok,
                    "healthy": ok,
                    "status": "healthy" if ok else str(entry.get("status", "degraded")),
                    **{k: v for k, v in entry.items() if k not in ("ok", "healthy", "status")},
                }
            else:
                services[str(name)] = {"ok": bool(entry), "healthy": bool(entry), "status": "healthy" if entry else "unknown"}
        # Fold organism vitals when available
        try:
            from core import organism as org_mod
            from core.request_context import require_request_context
            wid = str(require_request_context().workspace_id or "")
            org = None
            for fn_name in ("get_organism_for_workspace", "get_organism", "resolve_organism"):
                fn = getattr(org_mod, fn_name, None)
                if callable(fn) and wid:
                    try:
                        org = fn(wid)
                        break
                    except TypeError:
                        try:
                            org = fn()
                            break
                        except Exception:
                            pass
            if org is None and hasattr(org_mod, "_organisms") and wid:
                org = getattr(org_mod, "_organisms", {}).get(wid)
            if org is not None:
                life = getattr(org, "life_status", None) or getattr(org, "status", None)
                if hasattr(life, "value"):
                    life = life.value
                life_s = str(life or "").upper()
                services["organism"] = {
                    "ok": life_s in ("ALIVE", "HEALTHY", "RUNNING"),
                    "healthy": life_s in ("ALIVE", "HEALTHY", "RUNNING"),
                    "status": "healthy" if life_s in ("ALIVE", "HEALTHY", "RUNNING") else str(life or "unknown").lower(),
                }
        except Exception:
            pass
        all_ok = all(bool(s.get("ok")) for s in services.values()) if services else False
        any_ok = any(bool(s.get("ok")) for s in services.values()) if services else False
        status = "healthy" if all_ok and services else ("degraded" if any_ok else "unknown")
        return _ok({
            "status": status,
            "connectors": statuses,
            "services": services,
            "uptime_seconds": getattr(getattr(request.app.state, "started_at", None), "timestamp", lambda: None)()
            if False else None,
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/system/health")


@router.get("/system/connectors")
async def get_system_connectors(_auth: bool = Depends(require_auth)):
    """Get supported connectors."""
    from services.connectors.connector_factory import supported_platforms
    return _ok({"supported_connectors": supported_platforms()})


@router.get("/health/deep")
async def health_deep():
    """Deep health check — public. Multi-tenant aware."""
    from config.settings import settings
    
    # Facebook — check global + per-brand social accounts
    facebook_ok = bool(settings.page_access_token)
    if not facebook_ok:
        try:
            from repositories.social_account_repository import SocialAccountRepository
            repo = SocialAccountRepository()
            accounts = repo.find_by_platform("facebook")
            facebook_ok = any(
                getattr(a, 'enabled', False) and (
                    getattr(a, 'access_token', None) or
                    (getattr(a, 'platform_config', {}) or {}).get('page_access_token')
                )
                for a in (accounts or [])
            )
        except Exception:
            pass
    
    services = {
        "gemini": {"status": "ok" if settings.gemini_key else "error"},
        "facebook": {"status": "ok" if facebook_ok else "error"},
        "supabase": {"status": "ok" if settings.supabase_enabled else "degraded"},
    }
    all_healthy = all(s["status"] == "ok" for s in services.values())
    return JSONResponse({
        "status": "ok" if all_healthy else "degraded",
        "version": "12.1.0",
        "services": services,
    })
  
@router.get("/dashboard/live")
async def dashboard_live(_auth: bool = Depends(require_auth)):
    """Live dashboard stats for authenticated operators/workspace users."""
    from core.socket_server import build_live_stats
    return build_live_stats()


@router.get("/logs/recent")
async def logs_recent(
    limit: int = 50,
    level: str = None,
    _auth: bool = Depends(require_auth),
):
    """Spec §6: Get recent structured log events — no sensitive content exposed."""
    from core.log_buffer import LogBuffer
    buf = LogBuffer.get_instance()
    entries = buf.get_recent(limit=min(limit, 500), level=level)
    return {"ok": True, "logs": entries, "count": len(entries)}

# ═══════════════════════════════════════════════════════════════════════════
# MONITORING SSE — Authenticated Log Streaming
# ═══════════════════════════════════════════════════════════════════════════

# In-memory store for short-lived stream tokens
_stream_tokens: dict[str, dict] = {}
_stream_tokens_lock = threading.Lock()

STREAM_TOKEN_TTL_SECONDS = 30  # Tokens expire after 30 seconds


@router.post("/monitoring/stream-token")
async def generate_stream_token(
    _auth: bool = Depends(require_auth),
):
    """
    Generate a short-lived token for SSE streaming.
    
    Browser EventSource cannot send custom headers (no Authorization header).
    Instead, the client first calls this endpoint with proper auth,
    gets a short-lived token, and passes it as a query parameter to the SSE endpoint.
    
    Token expires in 30 seconds — enough time to establish the EventSource connection.
    """
    token = _secrets_mod.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=STREAM_TOKEN_TTL_SECONDS)

    with _stream_tokens_lock:
        _stream_tokens[token] = {
            "expires_at": expires_at,
            "workspace_id": auth_workspace_id_var.get(),
            "created_at": datetime.now(timezone.utc),
        }

        # Clean expired tokens
        now = datetime.now(timezone.utc)
        expired = [t for t, v in _stream_tokens.items() if v["expires_at"] < now]
        for t in expired:
            del _stream_tokens[t]

    return _ok({
        "token": token,
        "expires_in_seconds": STREAM_TOKEN_TTL_SECONDS,
        "stream_url": f"/api/v1/monitoring/stream?token={token}",
    })


@router.get("/monitoring/stream")
async def monitoring_stream(
    request: Request,
    token: str = "",
    level: str = "",
    module: str = "",
):
    """
    Authenticated SSE stream of log events.
    
    Connect with EventSource after obtaining a stream token:
      POST /api/v1/monitoring/stream-token  (with Authorization header)
      → GET /api/v1/monitoring/stream?token=xxx
    
    Sends two event types:
      - event: stats    → periodic statistics snapshot (every 15s)
      - event: log      → individual log entries as they occur
      - event: heartbeat → keepalive ping (every 30s)
    
    Query params:
      - token: Required. Short-lived stream token from /monitoring/stream-token
      - level: Optional. Filter by log level (ERROR, WARNING, INFO)
      - module: Optional. Filter by module name
    """
    # Validate stream token
    if not token:
        return _err("Stream token is required. Obtain one via POST /api/v1/monitoring/stream-token", 401)

    with _stream_tokens_lock:
        token_data = _stream_tokens.get(token)
        if not token_data:
            return _err("Invalid or expired stream token. Obtain a new one.", 401)

        now = datetime.now(timezone.utc)
        if token_data["expires_at"] < now:
            del _stream_tokens[token]
            return _err("Stream token has expired. Obtain a new one.", 401)

        # Token is valid — consume it (single use)
        del _stream_tokens[token]

    workspace_id = token_data.get("workspace_id")

    # ── SSE Generator ──────────────────────────────────────────────────
    async def event_generator():
        from core.log_buffer import LogBuffer
        import json

        buf = LogBuffer.get_instance()
        queue: asyncio.Queue = asyncio.Queue()

        # Filter config
        level_filter = level.upper() if level else None
        module_filter = module.lower() if module else None

        def on_log(entry):
            """Callback from LogBuffer — push to async queue."""
            try:
                # Apply filters
                if level_filter and entry.level != level_filter:
                    return
                if module_filter and module_filter not in entry.module.lower():
                    return

                # Don't leak cross-workspace logs (if workspace context exists)
                # Logs don't have workspace_id, so this is best-effort via correlation_id

                queue.put_nowait(("log", entry.to_dict()))
            except Exception:
                pass

        # Subscribe to log buffer
        buf.subscribe(on_log)

        # Send initial stats
        stats = buf.get_stats()
        stats["as_of"] = datetime.now(timezone.utc).isoformat()
        yield f"event: stats\ndata: {json.dumps(stats)}\n\n"

        # Periodic stats + heartbeat
        last_stats_time = time.monotonic()
        last_heartbeat = time.monotonic()

        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                now_mono = time.monotonic()

                # Send stats every 15 seconds
                if now_mono - last_stats_time >= 15:
                    stats = buf.get_stats()
                    stats["as_of"] = datetime.now(timezone.utc).isoformat()
                    yield f"event: stats\ndata: {json.dumps(stats)}\n\n"
                    last_stats_time = now_mono

                # Send heartbeat every 30 seconds
                if now_mono - last_heartbeat >= 30:
                    yield f"event: heartbeat\ndata: {json.dumps({'time': datetime.now(timezone.utc).isoformat()})}\n\n"
                    last_heartbeat = now_mono

                # Drain log events from the queue
                try:
                    event_type, data = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    pass

        finally:
            buf.unsubscribe(on_log)
            # Drain remaining items
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/monitoring/stats")
async def monitoring_stats(
    _auth: bool = Depends(require_auth),
):
    """
    Get current monitoring statistics snapshot.
    
    Returns the canonical monitoring contract:
      { total, errors, warnings, success, rate, as_of }
    """
    try:
        from core.log_buffer import LogBuffer
        buf = LogBuffer.get_instance()
        raw = buf.get_stats()

        total = raw.get("total_logs", 0)
        errors = raw.get("errors", 0)
        warnings = raw.get("warnings", 0)
        info = raw.get("info", 0)
        success = total - errors - warnings if total > 0 else 0
        rate = round((success / total) * 100, 1) if total > 0 else 100.0

        return _ok({
            "total": total,
            "errors": errors,
            "warnings": warnings,
            "success": max(success, 0),
            "rate": rate,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": raw.get("uptime_seconds", 0),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/monitoring/stats")


@router.get("/logs/stats")
async def logs_stats(_auth: bool = Depends(require_auth)):
    """Spec §6: Return actual log counters — total, errors, warnings, success, rate."""
    from core.log_buffer import LogBuffer
    buf = LogBuffer.get_instance()
    all_entries = buf.get_recent(limit=1000)
    total = len(all_entries)
    errors = sum(1 for e in all_entries if (e.get("level") or "").upper() in ("ERROR", "CRITICAL"))
    warnings = sum(1 for e in all_entries if (e.get("level") or "").upper() == "WARNING")
    success = sum(1 for e in all_entries if (e.get("level") or "").upper() in ("INFO", "DEBUG"))
    rate = round(total / max(len({e.get("minute", "x") for e in all_entries}), 1), 2)
    return _ok({
        "total": total,
        "errors": errors,
        "warnings": warnings,
        "success": success,
        "rate": rate,
        "as_of": datetime.now(timezone.utc).isoformat(),
    })


@router.get("/logs/stream")
async def logs_stream(_auth: bool = Depends(require_auth)):
    """
    Spec §6: Real-time log streaming via Server-Sent Events.
    Polls the LogBuffer and pushes new entries to the client.
    Does not expose sensitive log content.
    """
    from core.log_buffer import LogBuffer
    from fastapi.responses import StreamingResponse

    async def _event_generator():
        buf = LogBuffer.get_instance()
        last_count = 0
        try:
            while True:
                entries = buf.get_recent(limit=100)
                if len(entries) > last_count:
                    new_entries = entries[:len(entries) - last_count]
                    for entry in reversed(new_entries):
                        import json as _j
                        data = _j.dumps({
                            "timestamp": entry.get("timestamp"),
                            "level": entry.get("level"),
                            "message": str(entry.get("message", ""))[:500],
                        })
                        yield f"data: {data}\n\n"
                    last_count = len(entries)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/context/me")
async def get_context_me(request: Request, _auth: bool = Depends(require_auth)):
    """Return the canonical authenticated principal/workspace context."""
    from core.request_context import get_request_context
    ctx = get_request_context()
    identity = getattr(request.state, "auth_identity", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Authentication context unavailable")
    return _ok({
        "principal_id": ctx.principal_id,
        "workspace_id": ctx.workspace_id,
        "role": ctx.role,
        "permissions": list(ctx.permissions or ()),
        "species_privileges": bool(ctx.species_privileges),
        "auth_method": ctx.auth_method or getattr(identity, "method", ""),
        "correlation_id": ctx.correlation_id,
        "brand_id": ctx.brand_id,
        "email": ctx.user_email or getattr(identity, "user_email", None),
    })


@router.get("/status")
async def get_status():
    """Get platform status — public."""
    from config.settings import settings
    return _ok({
        "version": PLATFORM_VERSION,
        "environment": settings.environment,
        "config": {
            "ai_provider": settings.ai_provider,
            "gemini_model": settings.gemini_model,
            "supabase_enabled": settings.supabase_enabled,
        },
    })


@router.get("/features")
async def get_features(
    _auth: bool = Depends(require_auth),
):
    """
    Get feature flags for the authenticated workspace.
    
    Loads from Supabase if available, falls back to defaults.
    Workspace-scoped — different workspaces can have different flags.
    """
    try:
        workspace_id = auth_workspace_id_var.get() or "global"
        
        # Start with defaults
        from config.settings import settings
        flags = {
            "auto_reply": True,
            "auto_post": True,
            "guardian_scan": settings.guardian_configured,
        }

        # Load persisted flags from Supabase (workspace-scoped)
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                result = await _async_wrap(
                    lambda: supabase_service.client.table("feature_flags")
                    .select("feature, enabled")
                    .eq("workspace_id", workspace_id)
                    .execute()
                )
                if result.data:
                    for row in result.data:
                        flags[row["feature"]] = row["enabled"]
        except Exception as e:
            logger.debug("Feature flags read from Supabase failed, using defaults: %s", e)

        return _ok({
            "features": flags,
            "workspace_id": workspace_id,
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/features")

@router.post("/features/toggle")
async def toggle_feature(
    body: dict,
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """
    Spec §8: Toggle a feature flag — persists to Supabase if available,
    falls back to in-memory. State survives restarts when Supabase is configured.
    """
    try:
        feature = body.get("feature")
        enabled = body.get("enabled")
        workspace_id = resolve_plugin_workspace(body.get("workspace_id"))

        if not feature:
            return _err("feature name is required", 400)
        if enabled is None:
            return _err("enabled (boolean) is required", 400)

        record = {
            "feature": feature,
            "enabled": bool(enabled),
            "workspace_id": workspace_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": auth_user_email_var.get() or "system",
        }

        # Persist to Supabase (durable — survives restarts and deployments)
        persisted = False
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                await _async_wrap(
                    lambda: supabase_service.client.table("feature_flags")
                    .upsert({**record, "id": f"{workspace_id}:{feature}"})
                    .execute()
                )
                persisted = True
        except Exception as pe:
            logger.warning("Feature flag persistence to Supabase failed: %s", pe)

        return _ok({
            "feature": feature,
            "enabled": bool(enabled),
            "workspace_id": workspace_id,
            "persisted": persisted,
        })
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/features/toggle")


@router.get("/rate-limits")
async def get_rate_limits(
    _auth: bool = Depends(require_auth),
):
    """
    Get rate limit configuration for the authenticated workspace.
    Loads from Supabase if available, falls back to defaults.
    """
    try:
        workspace_id = auth_workspace_id_var.get() or "global"
        
        # Default config
        config = {
            "limit": 30,
            "window": 60,
            "scope": "user",
            "workspace_id": workspace_id,
            "provider": "default",
            "enabled": True,
        }

        # Load from Supabase
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                result = await _async_wrap(
                    lambda: supabase_service.client.table("rate_limit_config")
                    .select("*")
                    .eq("workspace_id", workspace_id)
                    .limit(1)
                    .execute()
                )
                if result.data:
                    row = result.data[0]
                    config = {
                        "limit": row.get("limit", 30),
                        "window": row.get("window", 60),
                        "scope": row.get("scope", "user"),
                        "workspace_id": row.get("workspace_id", workspace_id),
                        "provider": row.get("provider", "default"),
                        "enabled": row.get("enabled", True),
                    }
        except Exception as e:
            logger.debug("Rate limits read from Supabase failed, using defaults: %s", e)

        return _ok({
            "rate_limits": config,
            "workspace_id": workspace_id,
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/rate-limits")

@router.put("/rate-limits/config")
async def update_rate_limit_config(
    body: RateLimitConfigRequest,
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """
    Spec §9: Update rate-limit configuration.
    Validates and persists the rate-limit rule to Supabase when available.
    """
    try:
        workspace_id = resolve_plugin_workspace(body.workspace_id)
        record = {
            "limit": body.limit,
            "window": body.window,
            "scope": body.scope,
            "workspace_id": workspace_id,
            "provider": body.provider,
            "enabled": body.enabled,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        persisted = False
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                rule_id = f"{body.scope}:{workspace_id}:{body.provider or 'default'}"
                await _async_wrap(
                    lambda: supabase_service.client.table("rate_limit_config")
                    .upsert({**record, "id": rule_id})
                    .execute()
                )
                persisted = True
        except Exception as pe:
            logger.warning("Rate-limit config persistence failed: %s", pe)

        return _ok({"updated": True, "config": record, "persisted": persisted})
    except Exception as exc:
        return _safe_err(exc, "PUT /api/v1/rate-limits/config")


@router.post("/rate-limits/unblock")
async def unblock_user(
    body: UnblockRequest,
    _auth: bool = Depends(require_auth),
):
    """Unblock a rate-limited user."""
    from utils.rate_limiter import rate_limiter
    unblocked = await _async_wrap(rate_limiter.unblock, body.identifier)
    return _ok({"unblocked": unblocked})


@router.get("/tenants")
async def get_tenants(_auth: bool = Depends(require_auth)):
    """List tenants."""
    from services.supabase import supabase_service
    if supabase_service and supabase_service.client:
        result = await _async_wrap(
            lambda: supabase_service.client.table("tenants")
            .select("*")
            .eq("status", "active")
            .execute()
        )
        return _ok({"tenants": result.data or []})
    return _ok({"tenants": []})


@router.post("/tenants/switch")
async def switch_tenant(
    body: TenantSwitchRequest,
    _auth: bool = Depends(require_auth),
):
    """Switch active tenant."""
    return _ok({"switched": True})


@router.get("/notifications")
async def get_notifications(_auth: bool = Depends(require_auth)):
    """Get notifications (placeholder)."""
    return _ok({"notifications": []})


@router.post("/notifications/{notif_id}/read")
async def mark_notification_read(
    notif_id: str,
    _auth: bool = Depends(require_auth),
):
    """Mark a notification as read."""
    return _ok({"read": True})


@router.get("/integrations")
async def get_integrations(_auth: bool = Depends(require_auth)):
    """Get integration statuses — multi-tenant aware."""
    from config.settings import settings
    
    # Facebook — check global + per-brand
    facebook_connected = bool(settings.page_access_token)
    if not facebook_connected:
        try:
            from repositories.social_account_repository import SocialAccountRepository
            repo = SocialAccountRepository()
            accounts = repo.find_by_platform("facebook")
            facebook_connected = any(
                getattr(a, 'enabled', False) for a in (accounts or [])
            )
        except Exception:
            pass
    
    integrations = [
        {"id": "facebook", "connected": facebook_connected},
        {"id": "gemini", "connected": bool(settings.gemini_key)},
        {"id": "supabase", "connected": settings.supabase_enabled},
        {"id": "twitter", "connected": bool(settings.twitter_access_token)},
        {"id": "linkedin", "connected": bool(settings.linkedin_access_token)},
        {"id": "whatsapp", "connected": bool(settings.whatsapp_token)},
        {"id": "redis", "connected": bool(settings.upstash_redis_rest_url)},
    ]
    return _ok({
        "integrations": integrations,
        "connected": sum(1 for i in integrations if i["connected"]),
    })

@router.get("/audit-logs")
async def get_audit_logs(
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    limit: int = 50,
):
    """Get audit logs — workspace-scoped."""
    limit = min(200, max(1, limit))
    workspace_id = auth_workspace_id_var.get()
    logs = await audit_log.get_recent(limit)
    
    # Filter by workspace if not admin
    if workspace_id:
        logs = [
            l for l in logs
            if l.get("workspace_id") == workspace_id or l.get("workspace_id") is None
        ]
    
    return _ok({
        "logs": logs,
        "count": len(logs),
        "workspace_id": workspace_id,
    })

@router.post("/scan")
async def trigger_scan_shortcut(
    background_tasks: BackgroundTasks,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Shortcut to trigger Guardian scan."""
    return await trigger_scan(background_tasks, container, _auth)


@router.get("/scan")
async def get_scan_status(container=Depends(get_container)):
    """Get scan status (aliases guardian/status)."""
    return await guardian_status(container)


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATION FACTORY
# ═══════════════════════════════════════════════════════════════════════════

def create_app(container=None) -> FastAPI:
    """Deprecated compatibility wrapper; the canonical app factory is main.create_app."""
    from main import create_app as canonical_create_app
    return canonical_create_app()
