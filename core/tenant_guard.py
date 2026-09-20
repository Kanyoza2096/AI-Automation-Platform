"""
Sprint 3 — Tenant isolation helpers.

App-layer checks that complement RLS. Never trust client-supplied
workspace_id alone when an auth identity is present.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("kanyoza.core.tenant_guard")


class TenantIsolationError(PermissionError):
    """Raised when a workspace boundary is crossed."""


def assert_workspace_access(
    identity: Any,
    resource_workspace_id: Optional[str],
    *,
    allow_species: bool = True,
) -> str:
    """
    Ensure identity may touch resource_workspace_id.
    Returns the workspace_id to use.
    """
    ws = (resource_workspace_id or "").strip()
    if not ws:
        raise TenantIsolationError("Resource missing workspace_id")

    if identity is None:
        # Unauthenticated path should not reach here in production APIs
        raise TenantIsolationError("No auth identity — workspace access denied")

    if allow_species and getattr(identity, "is_species", False):
        return ws

    id_ws = getattr(identity, "workspace_id", None)
    if not id_ws:
        raise TenantIsolationError("Identity has no workspace_id")

    if id_ws != ws:
        logger.warning(
            "Tenant isolation block: identity_ws=%s resource_ws=%s",
            id_ws, ws,
        )
        raise TenantIsolationError(
            f"Workspace mismatch: identity={id_ws} resource={ws}"
        )
    return ws


def filter_rows_for_workspace(rows: list, workspace_id: str, field: str = "workspace_id") -> list:
    """Defensive filter — never return other tenants' rows."""
    out = []
    for r in rows or []:
        if isinstance(r, dict):
            if r.get(field) == workspace_id:
                out.append(r)
        else:
            if getattr(r, field, None) == workspace_id:
                out.append(r)
    return out


def assert_same_workspace(a: Optional[str], b: Optional[str], *, label: str = "resource") -> None:
    if not a or not b:
        raise TenantIsolationError(f"{label}: missing workspace_id")
    if a != b:
        raise TenantIsolationError(f"{label}: workspace {a} != {b}")
