"""AI Control Plane read model — projection over the V71 workforce tables.

Read-only. Never executes, never grants authority. Reports the same
authoritative tables that v71_18 hydrates at runtime startup. Missing
tables, empty tables, and database failures all report UNKNOWN rather than
fabricated state. Invariants match src/contracts/aiControlPlane.ts exactly.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.dependencies import require_auth, require_workspace

logger = logging.getLogger("kanyoza.api.ai_control_plane")

router = APIRouter(prefix="/api/v1/ai-control-plane", tags=["ai-control-plane"])

CONTRACT = "kdo.ai-control-plane/v1"

INVARIANTS = (
    "AI may propose; deterministic governance and authorization decide.",
    "Agent identity does not grant permissions.",
    "Tool declaration does not grant execution authority.",
    "Model selection does not grant authorization.",
    "AI runs use the canonical KDO Run Model.",
    "Consequential actions use the canonical KCPG → Action Kernel path.",
    "Missing authoritative AI records remain UNKNOWN rather than fabricated.",
    "Workspace scope is contextual and must agree with authoritative session state.",
)

# Each kind maps to one V71 control-plane table plus the column names that
# carry the AIResourceRecord fields. Verified against migrations/089, 093,
# 095, 096 and the live schema:
#
#   agent_identities              → workspace_id, agent_id, name, status, created_at
#   agent_coordination_requests   → workspace_id, coordination_id, objective, status, created_at
#   agent_capability_permissions  → workspace_id, permission_id, capability, effect, created_at
#                                   (no `status` column — `effect` is the state column)
#   agent_plans                   → workspace_id, plan_id, objective, status, created_at
#   agent_budgets                 → workspace_id, budget_id, agent_id, status, created_at
#   agent_autonomous_runs         → workspace_id, run_id, objective, status, created_at
#   agent_evaluations             → workspace_id, evaluation_id, agent_id, status, created_at
_KIND_SOURCES: dict[str, dict[str, str]] = {
    "agent": {
        "table": "agent_identities",
        "id": "agent_id",
        "name": "name",
        "state_col": "status",
        "observed": "created_at",
        "provenance": "v71_1_agent_identity",
    },
    "team": {
        "table": "agent_coordination_requests",
        "id": "coordination_id",
        "name": "objective",
        "state_col": "status",
        "observed": "created_at",
        "provenance": "v71_8_multi_agent_coordination",
    },
    "tool": {
        "table": "agent_capability_permissions",
        "id": "permission_id",
        "name": "capability",
        # agent_capability_permissions has no `status` column. It carries
        # `effect` (allow/deny), which is the closest semantic match for
        # "is this capability currently granted?". If the column name turns
        # out to differ, this is the only field that needs updating.
        "state_col": "effect",
        "observed": "created_at",
        "provenance": "v71_2_agent_capability_permissions",
    },
    "policy": {
        "table": "agent_plans",
        "id": "plan_id",
        "name": "objective",
        "state_col": "status",
        "observed": "created_at",
        "provenance": "v71_4_agent_planning",
    },
    "budget": {
        "table": "agent_budgets",
        "id": "budget_id",
        "name": "agent_id",
        "state_col": "status",
        "observed": "created_at",
        "provenance": "v71_6_agent_budgets",
    },
    "run": {
        "table": "agent_autonomous_runs",
        "id": "run_id",
        "name": "objective",
        "state_col": "status",
        "observed": "created_at",
        "provenance": "v71_9_bounded_autonomous_operations",
    },
    "decision": {
        "table": "agent_evaluations",
        "id": "evaluation_id",
        "name": "agent_id",
        "state_col": "status",
        "observed": "created_at",
        "provenance": "v71_5_agent_evaluation",
    },
}


def _db():
    """Resolve the service-role Supabase client. Returns None if unavailable."""
    try:
        from services.supabase import supabase_service
        if supabase_service is None:
            logger.warning("AI control plane: supabase_service is None")
            return None
        service_client = getattr(supabase_service, "service_client", None)
        fallback_client = getattr(supabase_service, "client", None)
        if service_client is not None:
            return service_client
        logger.warning(
            "AI control plane: service_client is None; using fallback client "
            "(likely anon — RLS may block reads)"
        )
        return fallback_client
    except Exception as exc:
        logger.warning("AI control plane: Supabase client unavailable: %s", exc)
        return None


def _read_table(
    db, table: str, workspace_id: str, limit: int
) -> Optional[list[dict[str, Any]]]:
    """Read rows from one V71 table scoped to the workspace.

    Returns None when the table is unreachable or the query fails — the
    caller treats None as "no data available" and does not fabricate rows.
    Returns an empty list when the table is reachable but has no rows for
    this workspace.
    """
    if db is None:
        return None
    try:
        result = (
            db.table(table)
            .select("*")
            .eq("workspace_id", workspace_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return list(getattr(result, "data", None) or [])
    except Exception as exc:
        logger.warning(
            "AI control plane: read %s workspace=%s failed: %s",
            table,
            workspace_id,
            exc,
        )
        return None


def _project_rows(
    kind: str,
    spec: dict[str, str],
    rows: list[dict[str, Any]],
    workspace_id: str,
) -> list[dict[str, Any]]:
    """Map raw table rows to AIResourceRecord dicts."""
    out: list[dict[str, Any]] = []
    for row in rows:
        row_id = row.get(spec["id"]) or row.get("id") or "unknown"
        display_name = row.get(spec["name"]) or row.get("name") or str(row_id)
        state_value = row.get(spec["state_col"]) or "UNKNOWN"
        observed = row.get(spec["observed"]) or row.get("updated_at")
        out.append(
            {
                "kind": kind,
                "id": str(row_id),
                "workspaceId": workspace_id,
                "name": str(display_name),
                "state": "OBSERVED",
                "certification": "UNKNOWN",
                "observedAt": str(observed) if observed else None,
                "provenance": spec["provenance"],
                "rawState": str(state_value),
            }
        )
    return out


@router.get("/snapshot")
async def ai_control_plane_snapshot(
    request: Request,
    _auth: bool = Depends(require_auth),
    workspace_id: str = Depends(require_workspace),
    limit: int = Query(200, ge=1, le=500),
):
    """Aggregate read model over the V71 workforce control plane.

    One request returns every AI resource kind for the authenticated
    workspace. Unreachable tables report as UNKNOWN for that kind. The
    response shape matches src/contracts/aiControlPlane.ts.
    """
    db = _db()
    resources: list[dict[str, Any]] = []
    kind_status: dict[str, str] = {}

    for kind, spec in _KIND_SOURCES.items():
        rows = _read_table(db, spec["table"], workspace_id, limit)
        if rows is None:
            kind_status[kind] = "UNKNOWN"
            continue
        kind_status[kind] = "OBSERVED"
        resources.extend(_project_rows(kind, spec, rows, workspace_id))

    # Truth is OBSERVED when every kind was reachable. If any kind is
    # UNKNOWN, the whole snapshot truth is UNKNOWN — a partial aggregate
    # must not report as fully observed.
    any_unknown = any(v == "UNKNOWN" for v in kind_status.values())
    any_observed = any(v == "OBSERVED" for v in kind_status.values())
    if any_unknown:
        truth = "UNKNOWN"
    elif any_observed:
        truth = "OBSERVED"
    else:
        truth = "UNKNOWN"

    return {
        "contract": CONTRACT,
        "truth": truth,
        "authority": "projection-only",
        "executionAuthority": "action-kernel",
        "resources": resources,
        "kindStatus": kind_status,
        "invariants": list(INVARIANTS),
    }


@router.get("/kind/{kind}")
async def ai_control_plane_kind(
    kind: str,
    request: Request,
    _auth: bool = Depends(require_auth),
    workspace_id: str = Depends(require_workspace),
    limit: int = Query(200, ge=1, le=500),
):
    """Read one kind of AI resource for the authenticated workspace."""
    spec = _KIND_SOURCES.get(kind)
    if spec is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown AI resource kind: {kind}"
        )

    db = _db()
    rows = _read_table(db, spec["table"], workspace_id, limit)
    if rows is None:
        return {
            "contract": CONTRACT,
            "kind": kind,
            "state": "UNKNOWN",
            "resources": [],
            "provenance": spec["provenance"],
        }

    return {
        "contract": CONTRACT,
        "kind": kind,
        "state": "OBSERVED",
        "resources": _project_rows(kind, spec, rows, workspace_id),
        "provenance": spec["provenance"],
    }
