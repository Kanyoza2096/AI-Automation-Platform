"""
Workspace Context Manager.

Provides thread-local and Flask-request-scoped storage for the current
workspace, brand, and social account. This allows deep domain services
to be multi-tenant without having to pass workspace_id through every function.
"""

import threading
from dataclasses import dataclass
from typing import Optional

from config.settings import get_settings

@dataclass
class WorkspaceContext:
    workspace_id: str
    brand_id: Optional[str] = None
    social_account_id: Optional[str] = None


# Thread-local storage as fallback outside Flask request context (e.g. background tasks)
_thread_local = threading.local()


def set_workspace_context(
    workspace_id: str,
    brand_id: Optional[str] = None,
    social_account_id: Optional[str] = None
) -> None:
    """Sets the workspace context in thread-local storage (FastAPI-compatible)."""
    context = WorkspaceContext(
        workspace_id=workspace_id,
        brand_id=brand_id,
        social_account_id=social_account_id
    )
    _thread_local.workspace_context = context


def get_workspace_context() -> WorkspaceContext:
    """
    Returns the current workspace context.
    Falls back to a default workspace if none is set.
    """
    if hasattr(_thread_local, 'workspace_context'):
        return _thread_local.workspace_context

    # Default fallback for backwards compatibility
    settings = get_settings()
    default_id = getattr(settings, 'default_workspace_id', 'default')
    if not default_id:
        default_id = 'default'

    return WorkspaceContext(workspace_id=default_id)


def get_current_workspace() -> str:
    """Helper to return just the current workspace ID."""
    return get_workspace_context().workspace_id


def get_current_brand() -> Optional[str]:
    """Helper to return the current brand ID if set."""
    return get_workspace_context().brand_id


def get_current_social_account() -> Optional[str]:
    """Helper to return the current social account ID if set."""
    return get_workspace_context().social_account_id


def init_workspace_middleware(app) -> None:
    """
    Registers a FastAPI middleware to extract workspace from request headers
    and set the thread-local workspace context for background tasks.

    Note: FastAPI request-scoped state is handled via ContextVar in api/v1.py.
    This middleware sets the thread-local fallback used by background workers.
    """
    from fastapi import Request
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response

    class WorkspaceMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            workspace_id = request.headers.get("X-Workspace-ID", "")
            brand_id = request.headers.get("X-Brand-ID") or None
            if not workspace_id:
                settings = get_settings()
                workspace_id = getattr(settings, "default_workspace_id", "default") or "default"
            set_workspace_context(workspace_id=workspace_id, brand_id=brand_id)
            return await call_next(request)

    app.add_middleware(WorkspaceMiddleware)
