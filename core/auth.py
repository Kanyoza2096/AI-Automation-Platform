"""
RBAC Auth — workspace isolation, role-based permission checks, and audit logging.

FastAPI-compatible version. No Flask dependencies.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, Optional


logger = logging.getLogger("kanyoza.core.auth")

# ---------------------------------------------------------------------------
# Canonical role compatibility facade
# ---------------------------------------------------------------------------

from core.authorization import CANONICAL_ROLES, at_least, role_level as _canonical_role_level

# Keep the old symbol importable for compatibility, but expose the canonical
# vocabulary.  ``editor`` is intentionally no longer a first-class role.
ROLES = list(CANONICAL_ROLES)

def role_level(role: str) -> int:
    return _canonical_role_level(role)


# ---------------------------------------------------------------------------
# Workspace Isolation — FastAPI dependency
# ---------------------------------------------------------------------------

class WorkspaceIsolation:
    """
    FastAPI dependency that ensures every request carries a valid workspace context.
    
    Usage:
        @router.get("/workspaces")
        async def list_workspaces(
            workspace: WorkspaceIsolation = Depends(),
        ):
            ...
    """

    def __init__(self, supabase_client=None) -> None:
        self._db = supabase_client

    async def __call__(self, request=None):
        """Resolve the already-authenticated workspace; never trust a header alone."""
        from core.request_context import require_request_context
        from core.workspace_context import set_workspace_context

        ctx = require_request_context()
        if not ctx.workspace_id:
            raise PermissionError("Authenticated principal has no workspace scope")

        brand_id = request.headers.get("X-Brand-ID") if request is not None else None
        set_workspace_context(
            workspace_id=str(ctx.workspace_id),
            brand_id=brand_id or ctx.brand_id,
            is_species=bool(ctx.species_privileges),
            auth_method=ctx.auth_method,
        )
        return str(ctx.workspace_id)


# ---------------------------------------------------------------------------
# RBAC Guard
# ---------------------------------------------------------------------------

class RBACGuard:
    """Decorator-based role-based access control guard."""

    @staticmethod
    def require_role(minimum_role: str, user_role: str = "viewer"):
        """Check if user_role meets the minimum_role requirement."""
        if not at_least(user_role, minimum_role):
            return False, f"Insufficient permissions. Required: {minimum_role}"
        return True, ""

    @staticmethod
    def require_workspace_owner(user_role: str = "viewer"):
        """Check if user is an owner."""
        return RBACGuard.require_role("owner", user_role)

    @staticmethod
    def require_admin(user_role: str = "viewer"):
        """Check if user is an admin or owner."""
        return RBACGuard.require_role("admin", user_role)


# ---------------------------------------------------------------------------
# Audit Logger
# ---------------------------------------------------------------------------

class AuditLogger:
    """Compatibility alias for core.audit_service.AuditService (table: audit_logs)."""

    def __init__(self, supabase_client=None) -> None:
        from core.audit_service import AuditService
        self._svc = AuditService(supabase_client=supabase_client)

    def set_supabase_client(self, client) -> None:
        self._svc.set_supabase_client(client)

    def log(self, *args, **kwargs):
        return self._svc.log(*args, **kwargs)

    def log_action(self, *args, **kwargs):
        return self._svc.log_action(*args, **kwargs)



# ---------------------------------------------------------------------------
# Module-level singletons (overridden by DI during app startup)
# ---------------------------------------------------------------------------

workspace_isolation = WorkspaceIsolation()
rbac = RBACGuard()
audit_logger = AuditLogger()
