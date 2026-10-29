"""
Safe plugin execution boundary.

Client-supplied workspace_id is never authoritative for ordinary principals.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from core.request_context import RequestContext, get_request_context, require_request_context


class PluginWorkspaceError(PermissionError):
    pass


def resolve_plugin_workspace(
    client_workspace_id: Optional[str] = None,
    *,
    ctx: Optional[RequestContext] = None,
) -> str:
    """
    For ordinary users: body.workspace_id is ignored.
    For species operators: optional cross-workspace selection is allowed.
    """
    context = ctx or get_request_context()
    if context is None:
        # Fail closed when no request context — do not default to client value
        raise PluginWorkspaceError(
            "Plugin execution requires authenticated RequestContext"
        )
    return context.effective_workspace(client_workspace_id)


def run_plugin(
    execute_fn: Callable[..., Dict[str, Any]],
    command: str,
    params: Dict[str, Any],
    *,
    client_workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Invoke a plugin execute(command, params, workspace_id=...) with enforced tenancy.
    """
    workspace_id = resolve_plugin_workspace(client_workspace_id)
    # Strip any client workspace from params so plugins cannot re-read it
    safe_params = {k: v for k, v in (params or {}).items() if k != "workspace_id"}
    return execute_fn(command, safe_params, workspace_id=workspace_id)
