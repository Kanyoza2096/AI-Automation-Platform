from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *
from workflows import AVAILABLE_WORKFLOWS

router = APIRouter()


def _filter_workflow_history_for_workspace(entries, workspace_id: str):
    """Return only records explicitly owned by the authenticated workspace."""
    if not workspace_id:
        return []
    return [entry for entry in entries or [] if entry.get("workspace_id") == workspace_id]


# ═══════════════════════════════════════════════════════════════════════════
# WORKFLOWS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/workflow")
async def list_workflows(
    request: Request,
    workflow_history: AsyncWorkflowHistory = Depends(get_workflow_history),
    _auth: bool = Depends(require_auth),
):
    """List available workflows with last run info for the active workspace."""
    workspace_id = await require_workspace(request)
    history = _filter_workflow_history_for_workspace(
        await workflow_history.get_all(), workspace_id,
    )
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
    _auth=Depends(require_workspace_operator),
):
    """Legacy all-brands workflow execution is disabled.

    The old implementation ignored the requested workflow name and triggered
    every registered brand. Use the governed single-brand command instead.
    """
    return _err(
        "Global workflow execution is disabled; use the governed brand-specific trigger.",
        410,
    )


@router.get("/workflow/status")
async def workflow_status(
    request: Request,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        from repositories.brand_repository import BrandRepository
        workspace_id = await require_workspace(request)
        brand_repo = await _async_wrap(container.resolve, BrandRepository)
        workspace_brands = await _async_wrap(brand_repo.find_by_workspace, workspace_id)
        brand_ids = {brand.id for brand in workspace_brands}
        manager = await _async_wrap(container.resolve, BrandSchedulerManager)
        all_status = await _async_wrap(manager.get_all_status)
        all_status = [status for status in all_status if status.get("brand_id") in brand_ids]
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
    request: Request,
    workflow_history: AsyncWorkflowHistory = Depends(get_workflow_history),
    _auth: bool = Depends(require_auth),
    limit: int = 20,
):
    """Get workflow run history — workspace-scoped."""
    try:
        limit = min(50, max(1, limit))
        workspace_id = await require_workspace(request)
        entries = _filter_workflow_history_for_workspace(
            await workflow_history.get_recent(limit), workspace_id,
        )

        return _ok({
            "history": entries,
            "count": len(entries),
            "workspace_id": workspace_id,
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/workflow/history")


@router.post("/workflow/pause")
async def workflow_pause(_auth=Depends(require_workspace_operator)):
    """Global scheduler pause is disabled until workspace-scoped pause exists."""
    return _err(
        "Global scheduler pause is disabled until workspace-scoped pause/resume is available.",
        410,
    )


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
    _auth=Depends(require_workspace_operator),
):
    """History-only cancellation is disabled; it did not cancel durable work."""
    return _err(
        "Workflow cancellation is unavailable until a durable workspace-scoped cancellation contract exists.",
        410,
    )


@router.post("/workflow/{workflow_id}/rollback")
async def rollback_workflow(
    workflow_id: str,
    _auth=Depends(require_workspace_operator),
):
    """History-only rollback is disabled; it did not reverse executed work."""
    return _err(
        "Workflow rollback is unavailable until a durable workspace-scoped rollback contract exists.",
        410,
    )
