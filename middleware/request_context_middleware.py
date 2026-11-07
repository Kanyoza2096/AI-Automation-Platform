"""
Canonical HTTP middleware:

  Authentication → RequestContext → Workspace binding

Public/health paths skip auth. Everything under /api/ requires credentials
unless explicitly allowlisted — including webhook ingress, which is
authenticated by the source secret at the handler, not by a user token.
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from typing import Callable, Set

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.request_context import (
    bind_from_auth_identity,
    clear_request_context,
    set_request_context,
)
from core.workspace_context import clear_workspace_context, set_workspace_context

logger = logging.getLogger("kanyoza.middleware.request_context")

# Paths that never require authentication
PUBLIC_PREFIXES: Set[str] = {
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
    "/api/v1/status",
    "/api/v1/health",
    "/api/v1/system-health",    # ← add
    "/api/v1/guardian/status",  # ← add
}

SERVICE_AUTH_ONLY_EXACT: Set[str] = {
    # These routes authenticate the KCPG service credential in their own
    # FastAPI dependency. They deliberately do not bind a tenant workspace;
    # the exchange endpoint validates the supplied human Supabase JWT itself.
    "/api/v1/auth/exchange",
    "/api/v1/auth/workspace",
    "/api/v1/system/auth/validate",
}


PUBLIC_EXACT: Set[str] = {
    "/",
    "/api/v1/architecture/laws",
    "/api/v1/architecture/health-model",
    "/api/v1/status",
    "/director/health", 
}


def _is_webhook_ingress(path: str) -> bool:
    """Webhook ingress is authenticated by the source secret, not a user token.

    These paths carry `X-KDO-Webhook-Secret` and are validated inside the
    business automation ingress handler. The user-token middleware must not
    intercept them, or external systems cannot deliver events.
    """
    if path.startswith("/api/v1/business-automation/event-sources/") and path.endswith("/ingest"):
        return True
    if path.startswith("/api/v1/business-automation/lead-capture/"):
        return True
    return False


def _is_service_auth_only(path: str) -> bool:
    """Service-token-only endpoints are authenticated by route dependencies.

    They must bypass tenant context binding, not authentication: the route
    handlers retain their mandatory require_auth dependency.
    """
    return path in SERVICE_AUTH_ONLY_EXACT


def _is_public(path: str) -> bool:
    if _is_webhook_ingress(path):
        return True
    if path in PUBLIC_EXACT:
        return True
    for prefix in PUBLIC_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _resolve_supabase_client(container):
    """Resolve Supabase client from container or direct import."""
    try:
        from services.supabase import supabase_service
        client = supabase_service.client if supabase_service else None
        if client is None:
            client = getattr(supabase_service, "service_client", None)
        return client
    except Exception:
        pass

    try:
        return getattr(getattr(container, "supabase_service", None), "client", None)
    except Exception:
        return None


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Species Law: every non-public request gets AuthIdentity → RequestContext.
    Client X-Workspace-ID is only honored for species operators.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        correlation_id = (
            request.headers.get("X-Correlation-ID")
            or request.headers.get("x-correlation-id")
            or str(uuid.uuid4())
        )
        request.state.correlation_id = correlation_id

        if request.method == "OPTIONS" or _is_public(path) or _is_service_auth_only(path):
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            return response

        if not (
            path.startswith("/api/")
            or path.startswith("/director")
            or path.startswith("/plugins/")
        ):
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            return response

        try:
            identity = self._authenticate(request)
            if not identity.valid:
                return JSONResponse(
                    status_code=401,
                    content={"detail": identity.error or "Authentication required"},
                    headers={"X-Correlation-ID": correlation_id},
                )

            client_ws = (
                request.headers.get("X-Workspace-ID")
                or request.headers.get("x-workspace-id")
                or ""
            ).strip()
            ctx = bind_from_auth_identity(identity, correlation_id=correlation_id)
            container = getattr(request.app.state, "container", None)
            # ✅ FIX: Use helper to resolve Supabase client
            supabase_client = _resolve_supabase_client(container)

            if identity.is_species:
                if client_ws:
                    if len(client_ws) > 128 or any(c in client_ws for c in (" ", "\n", "\r")):
                        return JSONResponse(
                            status_code=400,
                            content={"detail": "Invalid X-Workspace-ID"},
                            headers={"X-Correlation-ID": correlation_id},
                        )
                    effective_ws = client_ws
                else:
                    try:
                        from config.settings import get_settings
                        effective_ws = (
                            getattr(get_settings(), "default_workspace_id", None)
                            or "platform"
                        )
                    except Exception:
                        effective_ws = "platform"

                from core.species_auth import validate_species_workspace

                # Fail closed: a privileged tenant selector must be validated before
                # it becomes request context. Never bind an arbitrary workspace when
                # the authoritative workspace registry is unavailable.
                if supabase_client is None:
                    logger.error("Supabase client unavailable — refusing species workspace selection: %s", effective_ws)
                    return JSONResponse(
                        status_code=503,
                        content={"detail": "Workspace authorization service unavailable"},
                        headers={"X-Correlation-ID": correlation_id},
                    )
                # Supabase's synchronous client must not block FastAPI's event loop.
                # Command Center fan-out can authenticate many requests at once; run
                # this authoritative check in the worker pool while preserving the
                # fail-closed result and per-request validation semantics.
                workspace_is_valid = await asyncio.to_thread(
                    validate_species_workspace, effective_ws, supabase_client
                )
                if not workspace_is_valid:
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Selected workspace does not exist or is not active"},
                        headers={"X-Correlation-ID": correlation_id},
                    )

                from core.request_context import RequestContext, set_request_context as _set

                ctx = RequestContext(
                    principal_id=ctx.principal_id,
                    workspace_id=str(effective_ws),
                    role=ctx.role,
                    permissions=ctx.permissions,
                    species_privileges=True,
                    auth_method=ctx.auth_method,
                    correlation_id=correlation_id,
                    brand_id=ctx.brand_id,
                    user_email=ctx.user_email,
                )
                _set(ctx)
            elif client_ws and not identity.is_species:
                if client_ws != str(ctx.workspace_id or ""):
                    logger.warning(
                        "Ignoring client X-Workspace-ID for non-species principal "
                        "header=%s bound=%s",
                        client_ws[:64],
                        ctx.workspace_id,
                    )

            if not (ctx.workspace_id or "").strip() and not identity.is_species:
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "No workspace bound to credentials. "
                        "Membership required; X-Workspace-ID is not authority."
                    },
                    headers={"X-Correlation-ID": correlation_id},
                )

            bound_ws = (ctx.workspace_id or "").strip() or "platform"
            set_workspace_context(
                workspace_id=bound_ws,
                is_species=bool(identity.is_species),
                auth_method=getattr(identity, "method", "") or "",
            )

            try:
                from api.support import auth_workspace_id_var
                auth_workspace_id_var.set(bound_ws)
            except Exception:
                pass

            request.state.auth_identity = identity
            request.state.request_context = ctx
            request.state.workspace_id = bound_ws

            # Phase 37.14 — lifecycle truth: an authenticated workspace must
            # have a started organism runtime before downstream health/status
            # routes report its state.  Creation alone is not "alive".
            try:
                from core.organism import organism_registry
                await organism_registry.ensure_started(bound_ws)
                request.state.organism_lifecycle = "ALIVE"
            except Exception as exc:
                # Fail closed for lifecycle reporting without turning every
                # authenticated request into a 500.  The vitals endpoint will
                # expose the unobservable condition explicitly.
                logger.warning(
                    "Organism lifecycle start unavailable workspace=%s error=%s",
                    bound_ws,
                    type(exc).__name__,
                )
                request.state.organism_lifecycle = "UNOBSERVABLE"

            try:
                response = await call_next(request)
            finally:
                clear_request_context()
                clear_workspace_context()

            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Workspace-ID"] = bound_ws
            return response

        except Exception as exc:
            logger.exception("RequestContext middleware failure")
            clear_request_context()
            clear_workspace_context()
            return JSONResponse(
                status_code=500,
                content={"detail": "Authentication pipeline failure"},
                headers={"X-Correlation-ID": correlation_id},
            )

    def _authenticate(self, request: Request):
        """Authenticate request and return identity object with attribute access."""
        from core.species_auth import SpeciesAuth, extract_token_from_request

        bearer_token, api_key_header = extract_token_from_request(request)
        container = getattr(request.app.state, "container", None)
        # ✅ FIX: Use helper to resolve Supabase client
        supabase_client = _resolve_supabase_client(container)
        key_store = getattr(request.app.state, "key_store", None)

        result = SpeciesAuth().validate(
            bearer_token=bearer_token or "",
            api_key_header=api_key_header or "",
            supabase_client=supabase_client,
            key_store=key_store,
        )

        if isinstance(result, dict):
            result["valid"] = bool(result.get("valid", False))
            result["is_species"] = bool(result.get("is_species", False))
            return SimpleNamespace(**result)

        if not hasattr(result, "valid"):
            return SimpleNamespace(
                valid=False,
                error="Invalid auth response",
                is_species=False,
                method="",
                principal_id=None,
                role=None,
                permissions=[],
                brand_id=None,
                user_email=None,
            )

        return result
