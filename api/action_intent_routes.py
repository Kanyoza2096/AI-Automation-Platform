"""KCPG -> KDO governed ActionIntent bridge.

The gateway is the operator authority front door; this route is the trusted
transport boundary into the Action Kernel. It accepts intents only from the
configured KCPG service credential and never executes a side effect inline.
"""
from __future__ import annotations

import hmac
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/control-plane", tags=["control-plane-action"])

class ActionIntentBody(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=256)
    action_type: str = Field(min_length=1, max_length=160)
    parameters: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = Field(default="low", pattern=r"^(low|medium|high|critical)$")
    decision_id: Optional[str] = None
    correlation_id: Optional[str] = None
    idempotency_key: str = Field(min_length=1, max_length=256)
    source: str = Field(default="kcpg", max_length=120)
    approved: bool = False

async def _auth(request: Request, _a=Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth)):
    identity = getattr(request.state, "auth_identity", None)
    if not identity or getattr(identity, "method", "") != "python_service":
        raise HTTPException(403, detail="KCPG service authentication required")
    return identity

@router.post("/action-intents", status_code=202)
async def enqueue_action_intent(body: ActionIntentBody, request: Request, identity=Depends(_auth)):
    # The service credential authenticates KCPG. The user/authority context was
    # already checked and fenced by KCPG; preserve it as immutable provenance.
    gateway_user_id = request.headers.get("X-User-Id", "").strip()
    gateway_role = request.headers.get("X-User-Role", "").strip()
    gateway_workspace = request.headers.get("X-Workspace-ID", "").strip()
    if not gateway_user_id or not gateway_workspace:
        raise HTTPException(400, detail="KCPG user and workspace provenance required")
    if gateway_workspace != body.workspace_id:
        raise HTTPException(403, detail="Workspace provenance mismatch")

    container = getattr(request.app.state, "container", None)
    db = getattr(getattr(container, "supabase_service", None), "client", None) if container else None
    from services.action_kernel import build_default_action_kernel
    kernel = build_default_action_kernel(db=db, allow_in_memory=False)
    result = await kernel.enqueue(
        workspace_id=body.workspace_id, action_type=body.action_type,
        parameters=body.parameters, risk_level=body.risk_level,
        decision_id=body.decision_id, correlation_id=body.correlation_id,
        idempotency_key=body.idempotency_key, source=f"kcpg:{body.source}",
        approved=body.approved,
    )
    result["operator"] = {"user_id": gateway_user_id, "role": gateway_role}
    result["authority_source"] = "kcpg"
    result["accepted_without_inline_side_effect"] = True
    return result

@router.post("/action-intents/{intent_id}/approve", status_code=200)
async def approve_action_intent(intent_id: str, request: Request, workspace_id: str, identity=Depends(_auth)):
    if not request.headers.get("X-User-Id", "").strip():
        raise HTTPException(400, detail="KCPG user provenance required")
    container = getattr(request.app.state, "container", None)
    db = getattr(getattr(container, "supabase_service", None), "client", None) if container else None
    from services.action_kernel import build_default_action_kernel
    kernel = build_default_action_kernel(db=db, allow_in_memory=False)
    try:
        result = await kernel.approve(workspace_id=workspace_id, intent_id=intent_id)
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    result["authority_source"] = "kcpg"
    return result
