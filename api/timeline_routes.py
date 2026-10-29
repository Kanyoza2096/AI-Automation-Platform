"""
Sprint 2 — Correlation / decision timeline + outcome APIs.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("kanyoza.api.timeline")
router = APIRouter(tags=["timeline"])


def _auth():
    from api.v1 import require_auth
    return require_auth


class OutcomeBody(BaseModel):
    outcome: str = Field(..., pattern=r"^(success|failure|partial)$")
    decision_id: Optional[str] = None
    correlation_id: Optional[str] = None
    platform_post_id: Optional[str] = None
    workspace_id: str = "default"
    score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    source: str = "human"


@router.get("/timeline/correlation/{correlation_id}")
async def timeline_by_correlation(
    correlation_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    _a=Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    identity = getattr(request.state, "auth_identity", None)
    from core.decision_gate import decision_gate
    decision = decision_gate.find_by_correlation(correlation_id)
    if decision:
        try:
            from core.tenant_guard import assert_workspace_access
            assert_workspace_access(identity, getattr(decision, "workspace_id", None))
        except Exception as exc:
            raise HTTPException(403, detail=str(exc))
    from core.unified_bus import get_bus_sync
    bus = get_bus_sync()
    if not bus:
        raise HTTPException(503, detail="Event bus unavailable")
    events = bus.get_correlation_timeline(correlation_id, limit=limit)
    return {
        "correlation_id": correlation_id,
        "decision": decision.to_dict() if decision else None,
        "events": events,
        "count": len(events),
    }


@router.get("/timeline/decision/{decision_id}")
async def timeline_by_decision(
    decision_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    _a=Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    from core.unified_bus import get_bus_sync
    from core.decision_gate import decision_gate

    decision = decision_gate.get(decision_id)
    identity = getattr(request.state, "auth_identity", None)
    if decision:
        try:
            from core.tenant_guard import assert_workspace_access
            assert_workspace_access(identity, getattr(decision, "workspace_id", None))
        except Exception as exc:
            raise HTTPException(403, detail=str(exc))
    bus = get_bus_sync()
    events = bus.get_decision_timeline(decision_id, limit=limit) if bus else []
    # merge correlation timeline if available
    corr_events = []
    if decision and decision.correlation_id and bus:
        corr_events = bus.get_correlation_timeline(decision.correlation_id, limit=limit)
    return {
        "decision": decision.to_dict() if decision else None,
        "events_by_decision": events,
        "events_by_correlation": corr_events,
    }


@router.get("/timeline/workspace/{workspace_id}/decisions")
async def list_workspace_decisions(
    workspace_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    _a=Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    identity = getattr(request.state, "auth_identity", None)
    if identity and not getattr(identity, "is_species", False):
        if identity.workspace_id and identity.workspace_id != workspace_id:
            raise HTTPException(403, detail="Workspace mismatch")
    from core.decision_gate import decision_gate
    rows = [d.to_dict() for d in decision_gate.list_for_workspace(workspace_id, limit=limit)]
    return {"workspace_id": workspace_id, "decisions": rows, "count": len(rows)}


@router.post("/timeline/outcome")
async def post_outcome(
    body: OutcomeBody,
    request: Request,
    _a=Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    """Human or system outcome attachment (closes the loop)."""
    from core.outcome_feedback import record_action_outcome
    identity = getattr(request.state, "auth_identity", None)
    try:
        from core.tenant_guard import assert_workspace_access
        workspace_id = assert_workspace_access(identity, body.workspace_id)
    except Exception as exc:
        raise HTTPException(403, detail=str(exc)) from exc

    result = await record_action_outcome(
        outcome=body.outcome,
        decision_id=body.decision_id,
        correlation_id=body.correlation_id,
        platform_post_id=body.platform_post_id,
        workspace_id=workspace_id,
        score=body.score,
        source=body.source,
    )
    return result
