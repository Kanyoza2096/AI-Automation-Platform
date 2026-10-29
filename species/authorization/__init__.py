"""Species authorization helpers."""
from __future__ import annotations
try:
    from core.auth import RBACGuard, WorkspaceIsolation, role_level, ROLES
except ImportError:
    RBACGuard = WorkspaceIsolation = role_level = ROLES = None  # type: ignore
__all__ = ["RBACGuard", "WorkspaceIsolation", "role_level", "ROLES"]
