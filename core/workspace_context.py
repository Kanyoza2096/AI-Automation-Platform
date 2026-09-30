"""
Workspace Context Manager — Phase A.

Uses contextvars (async-safe) as the primary store.
Thread-local is only a fallback for sync worker threads.

CRITICAL (Species Law):
  X-Workspace-ID from the client is NEVER authoritative by itself.
  Middleware may set a brand_id hint, but workspace_id must already be
  bound by SpeciesAuth (token → workspace).

  For species/master tokens, an optional X-Workspace-ID selects which
  organism to operate on (operator privilege only).
"""

from __future__ import annotations

import logging
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger("kanyoza.core.workspace_context")


@dataclass
class WorkspaceContext:
    workspace_id: str
    brand_id: Optional[str] = None
    # Generic domain-resource references. Domain code owns the meaning of
    # each key; platform core only carries the trusted reference.
    resource_refs: Dict[str, str] = field(default_factory=dict)
    is_species: bool = False
    auth_method: str = ""


_ctx_var: ContextVar[Optional[WorkspaceContext]] = ContextVar(
    "kanyoza_workspace_context", default=None
)
_thread_local = threading.local()


def set_workspace_context(
    workspace_id: str,
    brand_id: Optional[str] = None,
    resource_refs: Optional[Dict[str, str]] = None,
    *,
    is_species: bool = False,
    auth_method: str = "",
) -> None:
    context = WorkspaceContext(
        workspace_id=workspace_id,
        brand_id=brand_id,
        resource_refs=dict(resource_refs or {}),
        is_species=is_species,
        auth_method=auth_method,
    )
    _ctx_var.set(context)
    _thread_local.workspace_context = context


def clear_workspace_context() -> None:
    _ctx_var.set(None)
    if hasattr(_thread_local, "workspace_context"):
        del _thread_local.workspace_context


def get_workspace_context() -> WorkspaceContext:
    ctx = _ctx_var.get()
    if ctx is not None:
        return ctx
    if hasattr(_thread_local, "workspace_context"):
        return _thread_local.workspace_context

    from config.settings import get_settings

    settings = get_settings()
    default_id = getattr(settings, "default_workspace_id", "default") or "default"
    return WorkspaceContext(workspace_id=default_id)


def get_current_workspace() -> str:
    return get_workspace_context().workspace_id


def get_current_brand() -> Optional[str]:
    return get_workspace_context().brand_id


def get_current_resource(resource_type: str) -> Optional[str]:
    """Return a trusted domain resource reference from the workspace context.

    The platform core deliberately does not interpret resource types. Domain
    modules may use stable keys such as ``social_account`` or ``patient``.
    """
    key = (resource_type or "").strip()
    if not key:
        return None
    return get_workspace_context().resource_refs.get(key)


def bind_auth_identity(identity) -> None:
    """Bind context from SpeciesAuth AuthIdentity after successful validation."""
    if not identity or not getattr(identity, "valid", False):
        return

    if identity.is_species:
        from config.settings import get_settings

        settings = get_settings()
        default_id = getattr(settings, "default_workspace_id", "default") or "default"
        set_workspace_context(
            workspace_id=default_id,
            is_species=True,
            auth_method=identity.method,
        )
        return

    ws = (identity.workspace_id or "").strip()
    if not ws:
        # A non-species identity without a workspace is not a tenant context.
        # Never manufacture a default tenant from authentication metadata.
        raise PermissionError("Authenticated identity has no workspace_id")
    set_workspace_context(
        workspace_id=ws,
        is_species=False,
        auth_method=identity.method,
    )
    # Phase C — ensure this workspace has a living organism
    try:
        from core.organism import organism_registry
        organism_registry.get_or_create(ws)
    except Exception:
        pass


def init_workspace_middleware(app) -> None:
    """Brand hint for tenants; species operators may select workspace via header."""
    from starlette.middleware.base import BaseHTTPMiddleware

    class WorkspaceMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            brand_id = request.headers.get("X-Brand-ID") or None
            existing = _ctx_var.get()

            if existing and existing.is_species:
                header_ws = request.headers.get("X-Workspace-ID", "").strip()
                if header_ws:
                    set_workspace_context(
                        workspace_id=header_ws,
                        brand_id=brand_id,
                        is_species=True,
                        auth_method=existing.auth_method,
                    )
                elif brand_id:
                    set_workspace_context(
                        workspace_id=existing.workspace_id,
                        brand_id=brand_id,
                        is_species=True,
                        auth_method=existing.auth_method,
                    )
            elif existing and not existing.is_species:
                if brand_id:
                    set_workspace_context(
                        workspace_id=existing.workspace_id,
                        brand_id=brand_id,
                        is_species=False,
                        auth_method=existing.auth_method,
                    )

            return await call_next(request)

    app.add_middleware(WorkspaceMiddleware)
    logger.info("Workspace middleware registered (Phase A: header not authoritative)")


def require_workspace_context(*, allow_default: bool = False) -> WorkspaceContext:
    """
    Fail-closed workspace resolution for tenant-scoped operations.

    allow_default=False (default): raise if no context was bound by auth middleware.
    Species/master paths must call set_workspace_context after validating membership.
    """
    ctx = _ctx_var.get()
    if ctx is not None:
        return ctx
    if hasattr(_thread_local, "workspace_context"):
        return _thread_local.workspace_context

    if allow_default:
        return get_workspace_context()

    raise PermissionError(
        "No trusted WorkspaceContext bound. "
        "Authenticate and bind workspace via SpeciesAuth/JWT — "
        "X-Workspace-ID alone is not authority."
    )


def assert_workspace_matches(resource_workspace_id: Optional[str]) -> None:
    """Defense in depth: resource workspace must match bound context (unless species)."""
    if not resource_workspace_id:
        raise PermissionError("Resource missing workspace_id")
    ctx = require_workspace_context(allow_default=False)
    if ctx.is_species:
        return
    if str(ctx.workspace_id) != str(resource_workspace_id):
        raise PermissionError(
            f"Workspace mismatch: context={ctx.workspace_id} resource={resource_workspace_id}"
        )

