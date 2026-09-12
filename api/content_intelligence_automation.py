"""Content Intelligence + bounded Business Automation control plane."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from api.dependencies import auth_workspace_id_var, require_auth_context
from core.security.deps import require_workspace_admin

router = APIRouter(prefix="/api/v1", tags=["content-intelligence", "business-automation"], dependencies=[Depends(require_auth_context)])

@router.post("/content-intelligence/analyze")
async def analyze_content_intelligence(body: dict):
    topic = str(body.get("topic") or "").strip()
    if not topic:
        raise HTTPException(422, "topic is required")
    from services.content_intelligence import content_intelligence_engine
    result = await content_intelligence_engine.analyze(
        topic, context=str(body.get("context") or ""), max_results=body.get("max_results", 8),
        fetch_top=body.get("fetch_top", 4), workspace_id=auth_workspace_id_var.get() or "platform")
    return {"ok": True, "intelligence": result.to_dict()}

@router.get("/business-automation/rules")
async def list_automation_rules():
    from services.business_automation import business_automation_engine
    ws = auth_workspace_id_var.get()
    if not ws:
        raise HTTPException(403, "workspace context required")
    rules = await business_automation_engine.list_rules(ws)
    return {"ok": True, "rules": [r.__dict__ for r in rules]}

@router.post("/business-automation/rules")
async def create_automation_rule(body: dict, _admin=Depends(require_workspace_admin)):
    from services.business_automation import business_automation_engine
    ws = auth_workspace_id_var.get()
    if not ws:
        raise HTTPException(403, "workspace context required")
    try:
        rule = await business_automation_engine.create_rule(ws, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"ok": True, "rule": rule.__dict__}

@router.post("/business-automation/run")
async def run_automation(body: dict, _admin=None):
    from services.business_automation import business_automation_engine
    ws = auth_workspace_id_var.get()
    if not ws:
        raise HTTPException(403, "workspace context required")
    dry_run = bool(body.get("dry_run", True))
    if not dry_run:
        from core.request_context import get_request_context
        ctx = get_request_context()
        if not ctx or not (ctx.species_privileges or ctx.role in {"owner", "admin"} or ctx.has_permission("admin")):
            raise HTTPException(403, "Workspace administrator privileges required for live automation")
    executions = await business_automation_engine.run(ws, event=body.get("event") or {},
        rule_id=body.get("rule_id"), dry_run=dry_run, approved=bool(body.get("approved", False)))
    return {"ok": True, "executions": [x.__dict__ for x in executions]}

@router.get("/business-automation/history")
async def automation_history(limit: int = 50):
    from services.business_automation import business_automation_engine
    ws = auth_workspace_id_var.get()
    if not ws:
        raise HTTPException(403, "workspace context required")
    history = await business_automation_engine.history(ws, limit)
    return {"ok": True, "history": [x.__dict__ for x in history]}
