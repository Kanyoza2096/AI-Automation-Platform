from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *
from workflows import AVAILABLE_WORKFLOWS

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# WORKFLOWS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/workflow")
async def list_workflows(
    workflow_history: AsyncWorkflowHistory = Depends(get_workflow_history),
    _auth: bool = Depends(require_auth),
):
    """List available workflows with last run info."""
    history = await workflow_history.get_all()
    last_runs = {}
    for entry in reversed(history):
        name = entry.get("workflow")
        if name and name not in last_runs:
            last_runs[name] = entry

    workflows = [
        {"name": n, **{k: v for k, v in m.items() if k != "class"}, "last_run": last_runs.get(n)}
        for n, m in AVAILABLE_WORKFLOWS.items()
    ]
    return _ok({"workflows": workflows})


@router.post("/workflow/run")
async def run_workflow(
    body: WorkflowRunRequest,
    container=Depends(get_container),
    workflow_history: AsyncWorkflowHistory = Depends(get_workflow_history),
    _auth: bool = Depends(require_auth),
):
    """Run a workflow for all brands."""
    try:
        name = body.workflow.strip().lower()
        if name not in AVAILABLE_WORKFLOWS:
            return _err(f"Unknown workflow: {name}. Available: {list(AVAILABLE_WORKFLOWS.keys())}", 400)

        from scheduler.brand_scheduler import BrandSchedulerManager
        manager = await _async_wrap(container.resolve, BrandSchedulerManager)
        result = await manager.trigger_all()  # async — await directly

        entry = {
            "workflow": name,
            "ok": result.get("ok", False),
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "brands_triggered": result.get("brands_triggered", 0),
        }
        await workflow_history.append(entry)

        if result.get("ok"):
            return _ok({
                "workflow": name,
                "brands_triggered": result.get("brands_triggered", 0),
                "results": result.get("results", {}),
            })
        failed = result.get("brands_failed") or []
        return _err(
            f"Workflow failed for {len(failed)} brand(s): {failed}",
            500,
        )
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/workflow/run")


@router.get("/workflow/status")
async def workflow_status(container=Depends(get_container), _auth: bool = Depends(require_auth)):
    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        manager = await _async_wrap(container.resolve, BrandSchedulerManager)
        all_status = await _async_wrap(manager.get_all_status)
        running = sum(1 for s in all_status if s.get("running"))
        return _ok({
            "status": "running" if running > 0 else "idle",
            "brands": all_status,
            "brands_running": running,
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/workflow/status")


@router.get("/workflow/history")
async def workflow_history_endpoint(
    workflow_history: AsyncWorkflowHistory = Depends(get_workflow_history),
    _auth: bool = Depends(require_auth),
    limit: int = 20,
):
    """Get workflow run history — workspace-scoped."""
    try:
        limit = min(50, max(1, limit))
        workspace_id = auth_workspace_id_var.get()

        entries = await workflow_history.get_recent(limit)

        if workspace_id:
            entries = [
                e for e in entries
                if e.get("workspace_id") == workspace_id or e.get("workspace_id") is None
            ]

        return _ok({
            "history": entries,
            "count": len(entries),
            "workspace_id": workspace_id,
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/workflow/history")


@router.post("/workflow/pause")
async def workflow_pause(container=Depends(get_container), _auth=Depends(require_workspace_operator)):
    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        manager = await _async_wrap(container.resolve, BrandSchedulerManager)
        await _async_wrap(manager.stop)
        return _ok({"status": "paused"})
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/workflow/pause")


@router.post("/workflow/trigger")
async def workflow_trigger(
    request: Request,
    body: dict = {},
    container=Depends(get_container),
    _auth=Depends(require_workspace_operator),
):
    """Trigger a workflow run for a specific brand."""
    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        from repositories.brand_repository import BrandRepository

        brand_id = body.get("brand_id", "") if body else ""

        if not brand_id:
            return _err("brand_id is required. Pass {'brand_id': 'your-brand-id'}", 400)

        brand_repo = await _async_wrap(container.resolve, BrandRepository)
        brand = await _async_wrap(brand_repo.find_by_id, brand_id)

        if not brand:
            return _err(f"Brand '{brand_id}' not found", 404)

        # The operator role alone is not sufficient: bind the target brand to
        # the authenticated workspace before resolving profiles or scheduling.
        workspace_id = await require_workspace(request)
        if getattr(brand, "workspace_id", None) != workspace_id:
            return _err("Brand not found in the authenticated workspace", 404)

        # Manual publishing must use the manager-owned scheduler so the
        # durable JobService and distributed lock dependencies are retained.
        # Constructing a one-off scheduler here would bypass that composition.
        manager = container.try_resolve(BrandSchedulerManager)
        if manager is None or not hasattr(manager, "trigger_for_brand"):
            return _err("Workspace scheduler is unavailable; manual trigger was not started", 503)

        result = await _async_wrap(manager.trigger_for_brand, brand_id)

        response = {
            "ok": result.get("ok", False),
            "brand": result.get("brand", getattr(brand, "name", "")),
            "topic": result.get("topic", ""),
            "platforms": result.get("platforms", []),
            "duration_ms": result.get("duration_ms"),
            "job_id": result.get("job_id"),
        }
        if not result.get("ok"):
            response["error"] = result.get("error", "Unknown error")

        return _ok(response)

    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/workflow/trigger")


@router.post("/workflow/{workflow_id}/cancel")
async def cancel_workflow(
    workflow_id: str,
    workflow_history: AsyncWorkflowHistory = Depends(get_workflow_history),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Cancel a running workflow."""
    found = await workflow_history.find_and_update(
        "id", workflow_id,
        {"status": "cancelled", "cancelled_at": datetime.now(timezone.utc).isoformat()},
    )
    if not found:
        found = await workflow_history.find_and_update(
            "topic", workflow_id,
            {"status": "cancelled", "cancelled_at": datetime.now(timezone.utc).isoformat()},
        )
    if not found:
        return _err("Workflow not found", 404)

    await append_audit(
        audit_log, "workflow_cancelled", f"workflow:{workflow_id}", status_str="success",
    )
    return _ok({"cancelled": True})


@router.post("/workflow/{workflow_id}/rollback")
async def rollback_workflow(
    workflow_id: str,
    workflow_history: AsyncWorkflowHistory = Depends(get_workflow_history),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Rollback a workflow."""
    found = await workflow_history.find_and_update(
        "id", workflow_id,
        {"status": "rolled_back", "rolled_back_at": datetime.now(timezone.utc).isoformat()},
    )
    if not found:
        found = await workflow_history.find_and_update(
            "topic", workflow_id,
            {"status": "rolled_back", "rolled_back_at": datetime.now(timezone.utc).isoformat()},
        )
    if not found:
        return _err("Workflow not found", 404)

    await append_audit(
        audit_log, "workflow_rolled_back", f"workflow:{workflow_id}", status_str="success",
    )
    return _ok({"rolled_back": True})
