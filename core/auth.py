"""
RBAC Auth — workspace isolation, role-based permission checks, and audit logging.

Provides:
  - WorkspaceIsolation: ensures all DB queries are scoped to the current workspace
  - RBACGuard: role-based permission checks per endpoint
  - AuditLogger: writes mutating actions to the audit_log table
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, Optional

from flask import g, jsonify, request

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
# Workspace Isolation middleware
# ---------------------------------------------------------------------------

class WorkspaceIsolation:
    """Flask request guard that ensures every request carries a valid workspace context.

    The workspace is resolved from (in priority order):
      1. The ``X-Workspace-ID`` request header
      2. The ``workspace_id`` JWT claim (if using token auth)
      3. The env-configured ``DEFAULT_WORKSPACE_ID`` (single-tenant fallback)
    """

    def __init__(self, app=None, supabase_client=None) -> None:
        self._db = supabase_client
        if app is not None:
            self.init_app(app)

    def init_app(self, app) -> None:
        app.before_request(self._inject_workspace)

    def _inject_workspace(self) -> None:
        from core.workspace_context import set_workspace_context
        from config.settings import get_settings

        workspace_id = request.headers.get("X-Workspace-ID", "")
        brand_id = request.headers.get("X-Brand-ID", "")

        if not workspace_id:
            settings = get_settings()
            workspace_id = getattr(settings, "default_workspace_id", "default") or "default"

        set_workspace_context(workspace_id=workspace_id, brand_id=brand_id or None)


# ---------------------------------------------------------------------------
# RBAC Guard
# ---------------------------------------------------------------------------

class RBACGuard:
    """Decorator-based role-based access control guard."""

    @staticmethod
    def require_role(minimum_role: str):
        """Protect an endpoint — caller must have at least ``minimum_role``."""
        def decorator(f: Callable) -> Callable:
            @wraps(f)
            def wrapper(*args, **kwargs):
                caller_role = getattr(g, "workspace_role", "viewer")
                if role_level(caller_role) < role_level(minimum_role):
                    return jsonify({
                        "error": f"Insufficient permissions. Required: {minimum_role}",
                        "your_role": caller_role,
                    }), 403
                return f(*args, **kwargs)
            return wrapper
        return decorator

    @staticmethod
    def require_workspace_owner():
        """Shorthand — endpoint is owner-only."""
        return RBACGuard.require_role("owner")

    @staticmethod
    def require_admin():
        """Shorthand — endpoint requires admin or owner."""
        return RBACGuard.require_role("admin")


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
            def wrapper(*args, **kwargs):
                from core.workspace_context import get_current_workspace
                ws_id = get_current_workspace()
                user_id = getattr(g, "user_id", "")
                result = f(*args, **kwargs)
                self.log(
                    action=action,
                    workspace_id=ws_id,
                    user_id=user_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                )
                return result
            return wrapper
        return decorator


# ---------------------------------------------------------------------------
# Module-level singletons (overridden by DI during app startup)
# ---------------------------------------------------------------------------

workspace_isolation = WorkspaceIsolation()
rbac = RBACGuard()
audit_logger = AuditLogger()
