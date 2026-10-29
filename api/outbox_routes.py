"""Sprint 3 — Publish outbox inspection (tenant-scoped)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1", tags=["outbox"])

class OutboxReconcile(BaseModel):
    resolution: str
    platform_post_id: str | None = None
    note: str = ""


@router.get("/outbox/{workspace_id}")
async def list_outbox(
    workspace_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    _a=Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    identity = getattr(request.state, "auth_identity", None)
    try:
        from core.tenant_guard import assert_workspace_access
        workspace_id = assert_workspace_access(identity, workspace_id)
    except Exception as exc:
        raise HTTPException(403, detail=str(exc))
    from core.publish_outbox import publish_outbox
    items = [i.to_dict() for i in publish_outbox.list_for_workspace(workspace_id, limit=limit)]
    return {"workspace_id": workspace_id, "items": items, "count": len(items)}


@router.get("/outbox/{workspace_id}/item/{item_id}")
async def get_outbox_item(
    workspace_id: str,
    item_id: str,
    request: Request,
    _a=Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    identity = getattr(request.state, "auth_identity", None)
    try:
        from core.tenant_guard import assert_workspace_access
        workspace_id = assert_workspace_access(identity, workspace_id)
    except Exception as exc:
        raise HTTPException(403, detail=str(exc))
    from core.publish_outbox import publish_outbox
    item = publish_outbox.get(item_id, workspace_id=workspace_id)
    if not item:
        raise HTTPException(404, detail="Outbox item not found for this workspace")
    return item.to_dict()


@router.post("/outbox/drain")
async def drain_outbox(
    request: Request,
    limit: int = Query(10, ge=1, le=50),
    _a=Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    identity = getattr(request.state, "auth_identity", None)
    if not identity or not getattr(identity, "is_species", False):
        raise HTTPException(403, detail="Species operator required")
    from core.publish_outbox import publish_outbox
    stats = await publish_outbox.drain_once(limit=limit)
    return stats


@router.post("/outbox/{workspace_id}/item/{item_id}/reconcile")
async def reconcile_outbox_item(workspace_id: str, item_id: str, body: OutboxReconcile, request: Request,
    _a=Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth)):
    identity = getattr(request.state, "auth_identity", None)
    try:
        from core.tenant_guard import assert_workspace_access
        workspace_id = assert_workspace_access(identity, workspace_id)
    except Exception as exc:
        raise HTTPException(403, detail=str(exc))
    if not identity or not getattr(identity, "is_species", False):
        raise HTTPException(403, detail="Species operator required")
    from core.publish_outbox import publish_outbox
    try:
        item = publish_outbox.reconcile(item_id, workspace_id, resolution=body.resolution,
                                         platform_post_id=body.platform_post_id, note=body.note)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    if not item:
        raise HTTPException(404, detail="Uncertain outbox item not found")
    return item.to_dict()
