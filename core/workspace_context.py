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
    """Sets the workspace context in Flask g (if available) or thread-local."""
    context = WorkspaceContext(
        workspace_id=workspace_id,
        brand_id=brand_id,
        social_account_id=social_account_id
    )
    
    try:
        from flask import has_request_context, g
        if has_request_context():
            g.workspace_context = context
            return
    except ImportError:
        pass
        
    _thread_local.workspace_context = context


def get_workspace_context() -> WorkspaceContext:
    """
    Returns the current workspace context.
    Falls back to a default workspace if none is set.
    """
    try:
        from flask import has_request_context, g
        if has_request_context():
            if hasattr(g, 'workspace_context'):
                return g.workspace_context
    except ImportError:
        pass
        
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
    Registers a Flask @before_request middleware to extract workspace
    from request headers and set the context.
    """
    from flask import request
    
    @app.before_request
    def set_context_from_request():
        workspace_id = request.headers.get("X-Workspace-ID")
        brand_id = request.headers.get("X-Brand-ID")
        
        # If not provided in header, fallback to default (single-tenant behavior)
        if not workspace_id:
            settings = get_settings()
            workspace_id = getattr(settings, 'default_workspace_id', 'default')
            if not workspace_id:
                workspace_id = 'default'
                
        set_workspace_context(workspace_id=workspace_id, brand_id=brand_id)
