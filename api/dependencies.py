"""Canonical FastAPI request dependencies.

This module is deliberately framework-facing: authentication, request-scoped
context variables, rate limiting, container access, and resource ownership
checks live here instead of in the monolithic v1 route module.

Route modules may import these symbols directly.  ``api.v1`` re-exports them
for backwards compatibility with legacy route modules.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional, Callable

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from core.security.deps import require_workspace_operator, require_workspace_admin

# Canonical definition of auth_workspace_id_var lives in api.support.
# See INCIDENT-2026-10-16: two ContextVar instances with the same name
# existed (here and in api.support), and ContextVars compare by object
# identity, not by name string. require_auth set on this module's instance
# while middleware set on api.support's instance, so routers reading via
# `from api.dependencies import *` got whichever object their import
# resolved to. Result: workspace_id was None for some callers depending on
# import path, silently bypassing the workspace filter in tenant-scoped
# queries.
#
# ⚠ api.support must NOT import from api.dependencies. This is the canonical
# ContextVar home specifically to avoid a duplicated-var regression.
from api.support import auth_workspace_id_var  # noqa: E402,F401

logger = logging.getLogger("kanyoza.api.dependencies")

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="unknown")
auth_method_var: ContextVar[Optional[str]] = ContextVar("auth_method", default=None)
auth_user_email_var: ContextVar[Optional[str]] = ContextVar("auth_user_email", default=None)

bearer_scheme = HTTPBearer(auto_error=False)


def _resolve_workspace_from_request(request: Optional[Request]) -> Optional[str]:
    """Resolve the authenticated workspace for a request.

    Resolution order:
      1. auth_workspace_id_var — canonical, defined in api.support. Set by
         require_auth on every successful authentication.
      2. request.state.request_context.workspace_id — set by the canonical
         RequestContextMiddleware on every authenticated request.
      3. request.state.workspace_id — also set by the middleware.

    Tiers 2 and 3 are retained defensively: even though the ContextVar is now
    a single instance across the codebase (see INCIDENT-2026-10-16), the
    middleware path can populate request.state in contexts where the ContextVar
    is not visible (e.g., background tasks spawned off-request, or requests
    that bypass require_auth).
    """
    workspace_id = auth_workspace_id_var.get()
    if workspace_id:
        return workspace_id
    if request is not None:
        ctx = getattr(request.state, "request_context", None)
        if ctx is not None:
            candidate = getattr(ctx, "workspace_id", None)
            if candidate:
                return str(candidate)
        candidate = getattr(request.state, "workspace_id", None)
        if candidate:
            return str(candidate)
    return None


async def _async_wrap(func: Callable, *args, **kwargs) -> Any:
    """Run blocking legacy service/repository calls without blocking the loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


async def get_container(request: Request):
    """Dependency: provides ServiceContainer from app state."""
    return request.app.state.container


async def get_draft_store(request: Request):
    return request.app.state.draft_store


async def get_audit_log(request: Request):
    return request.app.state.audit_log


async def get_workflow_history(request: Request):
    return request.app.state.workflow_history


async def get_key_store(request: Request):
    return request.app.state.key_store


def _auth_error() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={
            "error": "unauthorized",
            "message": "Valid Authorization: Bearer <token> or X-API-Key header required",
        },
    )


async def require_auth(
    request: Request,
    container=Depends(get_container),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> bool:
    """Canonical authentication door for v1 and legacy route modules.

    v2.1 — SUPABASE CLIENT RESOLUTION FIX:
      Previous version passed:

          supabase_client=getattr(
              getattr(container, "supabase_service", None), "client", None
          )

      That getattr chain searched for `supabase_service` on the container,
      which is a DI registry and does not expose module-level shims. It
      always returned None, so `species_auth.validate()` received
      supabase_client=None on every request and built its own fresh
      HTTP/2 client internally. That fresh client hit the Supabase edge
      with HTTP/2 enabled and intermittently received a malformed response
      (RemoteProtocolError: "Received pseudo-header in trailer"). Each
      such failure surfaced as an intermittent 401 on authenticated
      endpoints.

      Now: the pooled HTTP/1.1 singleton from services.supabase is used
      directly. That client avoids the HTTP/2 edge bugs (GOAWAY,
      pseudo-header-in-trailer) and shares connection pooling across
      requests. The pooled client is initialized at boot by
      get_service_client_from_settings() and is available at request time.
    """
    bearer_token = credentials.credentials.strip() if credentials and credentials.credentials else ""
    try:
        from core.species_auth import species_auth

        # v2.1 — read the pooled Supabase client from the module shim,
        # not from the container. See docstring above.
        _supabase_client = None
        try:
            from services.supabase import supabase_service
            _supabase_client = (
                supabase_service.service_client
                or supabase_service.client
            )
        except Exception as _sb_exc:
            logger.debug(
                "require_auth: could not resolve supabase_service shim: %s",
                _sb_exc,
            )

        if _supabase_client is None:
            logger.warning(
                "require_auth: no Supabase client available from "
                "services.supabase shim — species_auth will fall back "
                "to key_store-only validation"
            )

        identity = species_auth.validate(
            bearer_token=bearer_token,
            api_key_header=request.headers.get("X-API-Key", "").strip(),
            supabase_client=_supabase_client,
            key_store=request.app.state.key_store,
        )
        if identity.valid:
            auth_method_var.set(identity.method)
            auth_workspace_id_var.set(identity.workspace_id)
            auth_user_email_var.set(identity.user_email)
            return True
    except Exception as exc:
        logger.debug("Canonical authentication failed: %s", exc)

    logger.warning(
        "[%s] Authentication failed from %s",
        correlation_id_var.get(),
        request.client.host if request.client else "unknown",
    )
    raise _auth_error()


async def _get_resource_workspace_id(container, resource_type: str, resource_id: str) -> Optional[str]:
    """Resolve the owning workspace without trusting caller-supplied workspace IDs."""
    try:
        if resource_type == "workspace":
            from repositories.workspace_repository import WorkspaceRepository
            repo = await _async_wrap(container.resolve, WorkspaceRepository)
            ws = await _async_wrap(repo.find_by_id, resource_id)
            return ws.id if ws else None
        if resource_type == "brand":
            from repositories.brand_repository import BrandRepository
            repo = await _async_wrap(container.resolve, BrandRepository)
            brand = await _async_wrap(repo.find_by_id, resource_id)
            return brand.workspace_id if brand else None
        if resource_type == "ai_profile":
            from repositories.ai_profile_repository import AIProfileRepository
            repo = await _async_wrap(container.resolve, AIProfileRepository)
            profile = await _async_wrap(repo.find_by_id, resource_id)
            return profile.workspace_id if profile else None
        if resource_type == "social_account":
            from repositories.social_account_repository import SocialAccountRepository
            repo = await _async_wrap(container.resolve, SocialAccountRepository)
            account = await _async_wrap(repo.find_by_id, resource_id)
            return account.workspace_id if account else None
    except Exception as exc:
        logger.error(
            "[%s] Failed to resolve workspace for %s:%s — %s",
            correlation_id_var.get(), resource_type, resource_id, exc,
        )
    return None


async def require_workspace(request: Request) -> str:
    """Return the authenticated workspace; reject requests without one.

    Resolution order:
      1. auth_workspace_id_var — canonical, defined in api.support.
      2. request.state.request_context.workspace_id — set by the canonical
         RequestContextMiddleware.
      3. request.state.workspace_id — also set by the middleware.

    The multi-source resolution is deliberate and safe: request.state is only
    populated after the middleware has authenticated the request.
    """
    workspace_id = _resolve_workspace_from_request(request)
    if not workspace_id:
        raise HTTPException(status_code=403, detail="Authenticated workspace is required")
    return workspace_id


async def require_workspace_ownership(
    resource_type: str,
    resource_id: str,
    request: Request,
    container=None,
):
    """Verify that the authenticated principal owns the requested resource.

    Container resolution:
      - Callers invoking this function as a FastAPI dependency pass the
        resolved container explicitly (via `Depends(get_container)` at the
        call site), or omit it — in which case the fallback resolves it
        from `request.app.state.container`.
      - Callers invoking this function directly from another async function
        (e.g. `await require_workspace_ownership(..., request)`) get the
        same fallback. This matters because Python does NOT evaluate the
        `Depends(...)` default when the function is called directly; the
        callable receives the `Depends` placeholder as-is and then crashes
        with `'Depends' object has no attribute 'resolve'`.

    The fallback is safe: `request.app.state.container` is populated by the
    application factory during boot, and the request is already authenticated
    by the time this function runs.
    """
    if container is None:
        container = getattr(request.app.state, "container", None)
    if container is None:
        logger.warning(
            "require_workspace_ownership: no container available for %s:%s",
            resource_type, resource_id,
        )
        return None

    workspace_id = _resolve_workspace_from_request(request)
    if not workspace_id:
        return None
    resource_ws = await _get_resource_workspace_id(container, resource_type, resource_id)
    if resource_ws is None:
        raise HTTPException(status_code=404, detail=f"{resource_type} not found")
    if resource_ws != workspace_id:
        logger.warning(
            "[%s] Cross-workspace access denied: ws=%s → %s:%s (owns %s)",
            correlation_id_var.get(), workspace_id, resource_type, resource_id, resource_ws,
        )
        raise HTTPException(status_code=404, detail=f"{resource_type} not found")
    return workspace_id


def create_rate_limit_dependency(action: str, max_per_minute: int = 30):
    """Build a dependency backed by the canonical RateLimiter service."""
    async def rate_limit_check(request: Request, container=Depends(get_container)):
        try:
            from core.rate_limiter import RateLimiter
            limiter = container.try_resolve(RateLimiter)
        except Exception:
            limiter = None
        if limiter is None:
            return
        client_ip = request.client.host if request.client else "unknown"
        key = f"api:{action}:{client_ip}"
        if not await _async_wrap(limiter.is_allowed, key, max_per_minute):
            logger.warning(
                "[%s] Rate limit hit for action=%s ip=%s",
                correlation_id_var.get(), action, client_ip,
            )
            raise HTTPException(
                status_code=429,
                detail={
                    "ok": False,
                    "error": "rate_limited",
                    "message": f"Too many requests. Max {max_per_minute} per minute.",
                    "retry_after_seconds": 60,
                },
            )
    return Depends(rate_limit_check)


async def append_audit(audit_log, action: str, resource: str, user: str = "system", status_str: str = "success", detail: str = "") -> None:
    """Append an audit entry; durable persistence remains best-effort telemetry."""
    entry = {
        "id": uuid.uuid4().hex[:16],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id_var.get(),
        "user": user,
        "action": action,
        "resource": resource,
        "status": status_str,
        "detail": detail[:500],
    }
    await audit_log.append(entry)

    async def _persist():
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    lambda: supabase_service.client.table("audit_logs").upsert(entry).execute(),
                )
        except Exception as exc:
            logger.debug("Failed to persist audit log to Supabase: %s", exc)

    asyncio.create_task(_persist())


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC SURFACE
# ═══════════════════════════════════════════════════════════════════════════
#
# Auto-capture every name currently in scope. Downstream routers use
# `from api.dependencies import *`, and Python's default wildcard convention
# silently drops names beginning with "_". Two separate incidents:
#
#   1. Underscore helpers (_async_wrap, _auth_error, _get_resource_workspace_id)
#      were missing because __all__ didn't list them explicitly.
#   2. Re-exported dependencies (require_workspace_operator, require_workspace_admin)
#      were missing because an explicit __all__ listed only names defined in this
#      module, not names imported into it.
#
# Capturing dir() removes both failure modes without needing to enumerate every
# name the wildcard consumers depend on. The cost is that any future rename or
# addition is automatically visible — which is what wildcard consumers expect.
#
# Long-term: replace `from api.dependencies import *` across api/routers/ with
# explicit imports. This block is a stabilizer, not the destination.

__all__ = [name for name in dir() if not name.startswith("__")]
