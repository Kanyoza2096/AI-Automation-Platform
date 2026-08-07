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
# Roles (ordered by privilege level)
# ---------------------------------------------------------------------------

ROLES = ["viewer", "editor", "admin", "owner"]


def role_level(role: str) -> int:
    """Return integer privilege level — higher = more privileged."""
    try:
        return ROLES.index(role)
    except ValueError:
        return -1


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
        """Resolve workspace from request headers or default."""
        from core.workspace_context import set_workspace_context
        from config.settings import get_settings

        workspace_id = ""

        if request:
            workspace_id = request.headers.get("X-Workspace-ID", "")
            brand_id = request.headers.get("X-Brand-ID", "")

        if not workspace_id:
            settings = get_settings()
            workspace_id = getattr(settings, "default_workspace_id", "default") or "default"
            brand_id = ""

        set_workspace_context(workspace_id=workspace_id, brand_id=brand_id or None)
        return workspace_id


# ---------------------------------------------------------------------------
# RBAC Guard
# ---------------------------------------------------------------------------

class RBACGuard:
    """Decorator-based role-based access control guard."""

    @staticmethod
    def require_role(minimum_role: str, user_role: str = "viewer"):
        """Check if user_role meets the minimum_role requirement."""
        if role_level(user_role) < role_level(minimum_role):
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
    """Persists a record of every mutating action to the ``audit_log`` table."""

    def __init__(self, supabase_client=None) -> None:
        self._db = supabase_client

    def log(
        self,
        action: str,
        workspace_id: str,
        user_id: str = "",
        resource_type: str = "",
        resource_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        correlation_id: str = "",
    ) -> None:
        if not self._db:
            logger.debug(
                "AuditLog [%s] workspace=%s user=%s %s/%s",
                action, workspace_id, user_id, resource_type, resource_id,
            )
            return

        try:
            self._db.table("audit_log").insert({
                "id": f"audit-{uuid.uuid4().hex[:12]}",
                "action": action,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "metadata": json.dumps(metadata or {}),
                "correlation_id": correlation_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as exc:
            # Audit failures must never break the main flow
            logger.error("AuditLogger.log failed: %s", exc)

    def audit(
        self,
        action: str,
        resource_type: str = "",
        resource_id: str = "",
    ):
        """Decorator — auto-logs before calling the wrapped function."""
        def decorator(f: Callable) -> Callable:
            @wraps(f)
            async def async_wrapper(*args, **kwargs):
                from core.workspace_context import get_current_workspace
                ws_id = get_current_workspace()
                result = await f(*args, **kwargs) if asyncio.iscoroutinefunction(f) else f(*args, **kwargs)
                self.log(
                    action=action,
                    workspace_id=ws_id,
                    user_id="",
                    resource_type=resource_type,
                    resource_id=resource_id,
                )
                return result

            @wraps(f)
            def sync_wrapper(*args, **kwargs):
                from core.workspace_context import get_current_workspace
                ws_id = get_current_workspace()
                result = f(*args, **kwargs)
                self.log(
                    action=action,
                    workspace_id=ws_id,
                    user_id="",
                    resource_type=resource_type,
                    resource_id=resource_id,
                )
                return result

            return async_wrapper if asyncio.iscoroutinefunction(f) else sync_wrapper
        return decorator



# ---------------------------------------------------------------------------
# Module-level singletons (overridden by DI during app startup)
# ---------------------------------------------------------------------------

workspace_isolation = WorkspaceIsolation()
rbac = RBACGuard()
audit_logger = AuditLogger()
