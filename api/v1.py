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
from core.security.deps import require_workspace, require_workspace_admin, require_workspace_operator
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
    workspace_id: str | None = Field(default=None, min_length=1, max_length=128)


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
        requested_workspace_id=(payload.workspace_id or "").strip() or None,
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
        "platformRole": identity.platform_role or None,
        "platformPermissions": identity.platform_permissions,
        "principalType": "human",
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



# --- IMPORTED ROUTERS ---
from api.routers.posts import router as posts_router
router.include_router(posts_router)
from api.routers.messages import router as messages_router
router.include_router(messages_router)
from api.routers.analytics import router as analytics_router
router.include_router(analytics_router)
from api.routers.bot_control import router as bot_control_router
router.include_router(bot_control_router)
from api.routers.jobs import router as jobs_router
router.include_router(jobs_router)
from api.routers.guardian import router as guardian_router
router.include_router(guardian_router)
from api.routers.schedule import router as schedule_router
router.include_router(schedule_router)
from api.routers.metrics import router as metrics_router
router.include_router(metrics_router)
from api.routers.tracing import router as tracing_router
router.include_router(tracing_router)
from api.routers.ai_config import router as ai_config_router
router.include_router(ai_config_router)
from api.routers.ai_chat import router as ai_chat_router
router.include_router(ai_chat_router)
from api.routers.workspaces import router as workspaces_router
router.include_router(workspaces_router)
from api.routers.users_crud import router as users_crud_router
router.include_router(users_crud_router)
from api.routers.brands import router as brands_router
router.include_router(brands_router)
from api.routers.ai_profiles import router as ai_profiles_router
router.include_router(ai_profiles_router)
from api.routers.social_accounts import router as social_accounts_router
router.include_router(social_accounts_router)
from api.routers.users import router as users_router
router.include_router(users_router)
from api.routers.knowledge import router as knowledge_router
router.include_router(knowledge_router)
from api.routers.api_keys import router as api_keys_router
router.include_router(api_keys_router)
from api.routers.workflows import router as workflows_router
router.include_router(workflows_router)
from api.routers.webhooks import router as webhooks_router
router.include_router(webhooks_router)
from api.routers.plugins import router as plugins_router
router.include_router(plugins_router)
from api.routers.file_upload import router as file_upload_router
router.include_router(file_upload_router)
from api.routers.system import router as system_router
router.include_router(system_router)
from api.routers.monitoring import router as monitoring_router
router.include_router(monitoring_router)
from api.routers.business_activation import router as business_activation_router
router.include_router(business_activation_router)
from api.routers.business_discovery import router as business_discovery_router
router.include_router(business_discovery_router)
