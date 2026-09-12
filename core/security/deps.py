"""
FastAPI dependencies for canonical authentication + RequestContext binding.
"""

from __future__ import annotations

from typing import Optional
import logging

from fastapi import Header, HTTPException, Request

from core.request_context import RequestContext, bind_from_auth_identity, get_request_context
from core.authorization import at_least


async def require_auth_context(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_correlation_id: Optional[str] = Header(None, alias="X-Correlation-ID"),
) -> RequestContext:
    """Resolve the canonical SpeciesAuth identity and bind RequestContext.

    If canonical HTTP middleware already authenticated this request, reuse its
    immutable context rather than performing a second token/database lookup.
    """
    existing = getattr(request.state, "request_context", None)
    if existing is not None and getattr(existing, "workspace_id", None):
        return existing

    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()
    api_key = (x_api_key or "").strip()
    if not bearer and not api_key:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        from core.species_auth import species_auth
        container = getattr(request.app.state, "container", None)
        supabase_client = getattr(getattr(container, "supabase_service", None), "client", None)
        key_store = getattr(request.app.state, "key_store", None)
        identity = species_auth.validate(
            bearer_token=bearer,
            api_key_header=api_key,
            supabase_client=supabase_client,
            key_store=key_store,
        )
    except HTTPException:
        raise
    except Exception as exc:
        # Authentication failures and infrastructure failures are different
        # operational states. Do not turn a database outage into a misleading
        # 401; callers should retry a 503 instead.
        logger = logging.getLogger("kanyoza.security.deps")
        logger.exception("Authentication backend failure: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Authentication service unavailable") from exc

    if not identity.valid:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    correlation = x_correlation_id or request.headers.get("X-Correlation-ID") or ""
    if getattr(identity, "is_species", False):
        target = (request.headers.get("X-Workspace-ID") or "").strip()
        if target:
            from core.species_auth import validate_species_workspace
            if not validate_species_workspace(target, supabase_client):
                raise HTTPException(status_code=403, detail="Selected workspace does not exist or is not active")
            from core.request_context import RequestContext, set_request_context
            ctx = RequestContext(
                principal_id=str(identity.user_id or "species"),
                workspace_id=target,
                role="species",
                permissions=tuple(identity.permissions or ()),
                species_privileges=True,
                auth_method=identity.method,
                correlation_id=correlation,
                user_email=identity.user_email,
            )
            set_request_context(ctx)
            return ctx
    return bind_from_auth_identity(identity, correlation_id=correlation)


async def require_species_privileges(request: Request) -> RequestContext:
    """Require a platform/species principal for control-plane operations."""
    ctx = getattr(request.state, "request_context", None) or get_request_context()
    if ctx is None:
        ctx = await require_auth_context(request)
    if not getattr(ctx, "species_privileges", False):
        raise HTTPException(status_code=403, detail="Platform operator privileges required")
    return ctx


async def require_workspace_role(request: Request, minimum_role: str) -> RequestContext:
    """Require an authenticated workspace role at or above *minimum_role*.

    This is defense-in-depth for direct backend access; KCPG remains the
    authoritative control-plane command gate. Owner inherits all lower human
    workspace roles through the canonical authorization hierarchy.
    """
    ctx = getattr(request.state, "request_context", None) or get_request_context()
    if ctx is None:
        ctx = await require_auth_context(request)
    if getattr(ctx, "species_privileges", False):
        return ctx
    if not at_least(getattr(ctx, "role", "viewer"), minimum_role):
        raise HTTPException(status_code=403, detail=f"Workspace role {minimum_role} or above required")
    return ctx


async def require_workspace_operator(request: Request) -> RequestContext:
    """Require operator-level workspace authority or above."""
    return await require_workspace_role(request, "operator")


async def require_workspace_owner(request: Request) -> RequestContext:
    """Require owner-level workspace authority (species also qualifies)."""
    return await require_workspace_role(request, "owner")


async def require_workspace_admin(request: Request) -> RequestContext:
    """Require an owner/admin (or species operator) for workspace administration."""
    ctx = getattr(request.state, "request_context", None) or get_request_context()
    if ctx is None:
        ctx = await require_auth_context(request)
    if not (getattr(ctx, "species_privileges", False) or at_least(getattr(ctx, "role", "viewer"), "admin") or ctx.has_permission("admin")):
        raise HTTPException(status_code=403, detail="Workspace administrator privileges required")
    return ctx


async def require_workspace(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_correlation_id: Optional[str] = Header(None, alias="X-Correlation-ID"),
) -> str:
    """
    Trusted workspace_id for the request (never from X-Workspace-ID alone).

    Uses RequestContext from middleware when present; otherwise authenticates.
    """
    from core.workspace_context import get_workspace_context, require_workspace_context

    # Prefer context already bound by RequestContextMiddleware
    existing = getattr(request.state, "request_context", None)
    if existing is not None and getattr(existing, "workspace_id", None):
        return str(existing.workspace_id)

    try:
        ctx = require_workspace_context(allow_default=False)
        return str(ctx.workspace_id)
    except PermissionError:
        pass

    # Authenticate and bind
    rc = await require_auth_context(
        request, authorization=authorization, x_correlation_id=x_correlation_id
    )
    from core.workspace_context import set_workspace_context

    set_workspace_context(
        workspace_id=rc.workspace_id,
        is_species=bool(getattr(rc, "species_privileges", False)),
        auth_method=getattr(rc, "auth_method", "") or "",
    )
    if not rc.workspace_id:
        raise HTTPException(
            status_code=403,
            detail="No workspace bound to credentials",
        )
    return str(rc.workspace_id)


def assert_resource_workspace(resource_workspace_id: Optional[str]) -> None:
    """Raise 403 if resource workspace does not match trusted context."""
    from core.workspace_context import assert_workspace_matches

    try:
        assert_workspace_matches(resource_workspace_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
