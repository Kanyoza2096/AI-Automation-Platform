"""
FastAPI dependencies for canonical authentication + RequestContext binding.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Optional

from fastapi import Header, HTTPException, Request

from core.request_context import RequestContext, bind_from_auth_identity, get_request_context
from core.authorization import at_least

logger = logging.getLogger("kanyoza.security.deps")


def _bind_verified_gateway_human_context(request: Request, machine_ctx: RequestContext, correlation: str) -> Optional[RequestContext]:
    """Bind a short-lived KCPG HMAC actor envelope to a service-authenticated request."""
    if not getattr(machine_ctx, "species_privileges", False):
        return None
    names = {"timestamp": "x-kcpg-context-timestamp", "request_id": "x-kcpg-context-request-id", "workspace_id": "x-kcpg-context-workspace-id", "actor_id": "x-kcpg-context-actor-id", "actor_role": "x-kcpg-context-actor-role", "signature": "x-kcpg-context-signature"}
    values = {key: request.headers.get(header) for key, header in names.items()}
    present = [value is not None for value in values.values()]
    if not any(present):
        return None
    if not all(present):
        raise HTTPException(status_code=403, detail="incomplete_kcpg_context")
    secret = os.getenv("KCPG_CONTEXT_SIGNING_SECRET", "").strip()
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="kcpg_context_signing_not_configured")
    try:
        timestamp = int(values["timestamp"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=403, detail="invalid_kcpg_context_timestamp")
    if abs(int(time.time() * 1000) - timestamp) > 300000:
        raise HTTPException(status_code=403, detail="expired_kcpg_context")
    workspace_id = str(values["workspace_id"] or "").strip()
    actor_id = str(values["actor_id"] or "").strip()
    request_id = str(values["request_id"] or "").strip()
    actor_role = str(values["actor_role"] or "").strip().lower()
    if not workspace_id or not actor_id or not request_id:
        raise HTTPException(status_code=403, detail="invalid_kcpg_context_identity")
    if actor_role not in {"viewer", "operator", "admin", "owner"}:
        raise HTTPException(status_code=403, detail="invalid_kcpg_context_role")
    selected_workspace = str(request.headers.get("X-Workspace-ID") or "").strip()
    if not selected_workspace or selected_workspace != workspace_id:
        raise HTTPException(status_code=403, detail="kcpg_context_workspace_mismatch")
    canonical = "|".join((values["timestamp"], request_id, workspace_id, actor_id, actor_role))
    expected = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(values["signature"])):
        raise HTTPException(status_code=403, detail="invalid_kcpg_context_signature")
    ctx = RequestContext(principal_id=actor_id, workspace_id=workspace_id, role=actor_role, permissions=(), principal_type="human", auth_method="supabase_jwt", correlation_id=request_id or correlation, metadata={"authority_source": "verified_kcpg_hmac"})
    request.state.request_context = ctx
    from core.request_context import set_request_context
    set_request_context(ctx)
    return ctx


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
        trusted = _bind_verified_gateway_human_context(request, existing, x_correlation_id or request.headers.get("X-Correlation-ID") or "")
        return trusted or existing

    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()
    api_key = (x_api_key or "").strip()
    if not bearer and not api_key:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        from core.species_auth import species_auth
        
        # v2.1 — SUPABASE CLIENT RESOLUTION FIX:
        # Read the pooled Supabase client from the module shim, not the container.
        # The container is a DI registry and does not expose module-level shims.
        _supabase_client = None
        try:
            from services.supabase import supabase_service
            _supabase_client = (
                supabase_service.service_client
                or supabase_service.client
            )
        except Exception as exc:
            logger.debug("Could not resolve supabase_service shim: %s", exc)

        if _supabase_client is None:
            logger.warning(
                "require_auth_context: no Supabase client available from "
                "services.supabase shim — workspace validation may fail"
            )

        key_store = getattr(request.app.state, "key_store", None)
        identity = species_auth.validate(
            bearer_token=bearer,
            api_key_header=api_key,
            supabase_client=_supabase_client,
            key_store=key_store,
        )
    except HTTPException:
        raise
    except Exception as exc:
        # Authentication failures and infrastructure failures are different
        # operational states. Do not turn a database outage into a misleading
        # 401; callers should retry a 503 instead.
        logger.exception("Authentication backend failure: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Authentication service unavailable") from exc

    if not identity.valid:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    correlation = x_correlation_id or request.headers.get("X-Correlation-ID") or ""
    
    if getattr(identity, "is_species", False):
        target = (request.headers.get("X-Workspace-ID") or "").strip()
        if target:
            from core.species_auth import validate_species_workspace
            if not validate_species_workspace(target, _supabase_client):
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
            trusted = _bind_verified_gateway_human_context(request, ctx, correlation)
            return trusted or ctx

    resolved = bind_from_auth_identity(identity, correlation_id=correlation)
    trusted = _bind_verified_gateway_human_context(request, resolved, correlation)
    return trusted or resolved


async def require_species_privileges(request: Request) -> RequestContext:
    """Require a platform/species principal for control-plane operations."""
    ctx = getattr(request.state, "request_context", None) or get_request_context()
    if ctx is None:
        ctx = await require_auth_context(request)
    if not getattr(ctx, "species_privileges", False):
        raise HTTPException(status_code=403, detail="Platform operator privileges required")
    return ctx

async def require_platform_or_species_privileges(request: Request) -> RequestContext:
    """Allow machine species principals or explicitly granted human platform super-admins.

    Workspace owners/admins and platform operators do not qualify. Human grants
    are accepted only when the canonical Supabase-JWT context carries the
    server-resolved super_admin role; client metadata and workspace RBAC are
    never consulted here.
    """
    ctx = getattr(request.state, "request_context", None) or get_request_context()
    if ctx is None:
        ctx = await require_auth_context(request)

    is_human_platform_super_admin = (
        getattr(ctx, "principal_type", "") == "human"
        and getattr(ctx, "auth_method", "") == "supabase_jwt"
        and getattr(ctx, "platform_role", "") == "super_admin"
        and not getattr(ctx, "species_privileges", False)
        and ctx.has_permission("platform:control_plane:access")
    )
    if not (getattr(ctx, "species_privileges", False) or is_human_platform_super_admin):
        raise HTTPException(status_code=403, detail="Platform authority required")
    return ctx

async def require_platform_or_species_privileges(request: Request) -> RequestContext:
    """Allow machine species principals or explicitly granted human platform super-admins.

    Workspace owners/admins and platform operators do not qualify. Human grants
    are accepted only when the canonical Supabase-JWT context carries the
    server-resolved super_admin role; client metadata and workspace RBAC are
    never consulted here.
    """
    ctx = getattr(request.state, "request_context", None) or get_request_context()
    if ctx is None:
        ctx = await require_auth_context(request)

    is_human_platform_super_admin = (
        getattr(ctx, "principal_type", "") == "human"
        and getattr(ctx, "auth_method", "") == "supabase_jwt"
        and getattr(ctx, "platform_role", "") == "super_admin"
        and not getattr(ctx, "species_privileges", False)
        and ctx.has_permission("platform:control_plane:access")
    )
    if not (getattr(ctx, "species_privileges", False) or is_human_platform_super_admin):
        raise HTTPException(status_code=403, detail="Platform authority required")
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


async def require_human_workspace_admin(request: Request) -> RequestContext:
    """Require a human JWT-backed workspace admin or human platform super-admin.

    Machine API tokens and species service credentials must never administer
    website integration registrations, even if their workspace scopes contain
    the string "admin".
    """
    ctx = getattr(request.state, "request_context", None) or get_request_context()
    if ctx is None:
        ctx = await require_auth_context(request)
    is_human_jwt = (
        getattr(ctx, "principal_type", "") == "human"
        and getattr(ctx, "auth_method", "") == "supabase_jwt"
        and not getattr(ctx, "species_privileges", False)
    )
    is_platform_super_admin = (
        is_human_jwt
        and getattr(ctx, "platform_role", "") == "super_admin"
        and ctx.has_permission("platform:control_plane:access")
    )
    is_workspace_admin = is_human_jwt and at_least(getattr(ctx, "role", "viewer"), "admin")
    if not (is_platform_super_admin or is_workspace_admin):
        raise HTTPException(status_code=403, detail="Human workspace administrator authority required")
    return ctx


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
    """
    from core.workspace_context import get_workspace_context, require_workspace_context

    # 1. Prefer context already bound by RequestContextMiddleware
    existing = getattr(request.state, "request_context", None)
    if existing is not None and getattr(existing, "workspace_id", None):
        return str(existing.workspace_id)

    # 2. Try the existing workspace context variable
    try:
        ctx = require_workspace_context(allow_default=False)
        return str(ctx.workspace_id)
    except PermissionError:
        pass

    # 3. Authenticate and bind
    rc = await require_auth_context(
        request, authorization=authorization, x_correlation_id=x_correlation_id
    )
    
    if not rc.workspace_id:
        raise HTTPException(
            status_code=403,
            detail="No workspace bound to credentials",
        )
        
    from core.workspace_context import set_workspace_context
    set_workspace_context(
        workspace_id=rc.workspace_id,
        is_species=bool(getattr(rc, "species_privileges", False)),
        auth_method=getattr(rc, "auth_method", "") or "",
    )
    
    return str(rc.workspace_id)


def assert_resource_workspace(resource_workspace_id: Optional[str]) -> None:
    """Raise 403 if resource workspace does not match trusted context."""
    from core.workspace_context import assert_workspace_matches

    try:
        assert_workspace_matches(resource_workspace_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
