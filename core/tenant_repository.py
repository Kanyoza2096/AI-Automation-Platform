"""
Tenant-scoped repository helpers — Species Law.

Prefer:
  find_by_id(record_id, workspace_id)
over:
  find_by_id(record_id)
"""

from __future__ import annotations

from typing import Any, Optional


class WorkspaceRequiredError(ValueError):
    """Raised when a tenant operation is invoked without workspace_id."""


def require_workspace_id(workspace_id: Optional[str], *, op: str = "query") -> str:
    if not workspace_id or not str(workspace_id).strip():
        raise WorkspaceRequiredError(
            f"workspace_id is required for tenant-scoped {op}"
        )
    return str(workspace_id).strip()


def assert_row_workspace(row: Any, workspace_id: str, *, id_field: str = "id") -> None:
    """Raise PermissionError if row belongs to another workspace."""
    if row is None:
        return
    row_ws = None
    if isinstance(row, dict):
        row_ws = row.get("workspace_id")
    else:
        row_ws = getattr(row, "workspace_id", None)
    if row_ws is not None and str(row_ws) != str(workspace_id):
        rid = row.get(id_field) if isinstance(row, dict) else getattr(row, id_field, "?")
        raise PermissionError(
            f"Resource {rid} is not in workspace {workspace_id}"
        )
