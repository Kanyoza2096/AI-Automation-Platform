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

logger = logging.getLogger("kanyoza.api.dependencies")

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="unknown")
auth_method_var: ContextVar[Optional[str]] = ContextVar("auth_method", default=None)
auth_workspace_id_var: ContextVar[Optional[str]] = ContextVar("auth_workspace_id", default=None)
auth_user_email_var: ContextVar[Optional[str]] = ContextVar("auth_user_email", default=None)

bearer_scheme = HTTPBearer(auto_error=False)


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
    """Canonical authentication door for v1 and legacy route modules."""
    bearer_token = credentials.credentials.strip() if credentials and credentials.credentials else ""
    try:
        from core.species_auth import species_auth
        identity = species_auth.validate(
            bearer_token=bearer_token,
            api_key_header=request.headers.get("X-API-Key", "").strip(),
            supabase_client=getattr(getattr(container, "supabase_service", None), "client", None),
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


async def require_workspace_ownership(
    resource_type: str,
    resource_id: str,
    container=Depends(get_container),
):
    """Verify that the authenticated principal owns the requested resource."""
    workspace_id = auth_workspace_id_var.get()
    if workspace_id is None:
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
