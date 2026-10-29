"""
E2E publish + director diagnostic routes.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("kanyoza.api.e2e")
router = APIRouter(tags=["e2e"])


class PublishBody(BaseModel):
    workspace_id: str = "default"
    brand_id: Optional[str] = None
    platform: str = "facebook"
    topic: Optional[str] = None
    caption: Optional[str] = None
    dry_run: bool = True  # safe default
    risk_level: str = Field(default="low", pattern=r"^(trivial|low|medium|high|critical)$")
    durable: bool = False
    decision_id: Optional[str] = None


@router.post("/e2e/publish")
async def e2e_publish(
    body: PublishBody,
    request: Request,
    _auth: bool = Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    """
    E2E publish diagnostic surface.

    This route is disabled in production. A dry run may exercise the non-
    consequential planning/pipeline path in controlled environments. Any
    real publish is delegated to the existing governed Action Kernel helper;
    this route never creates a second execution authority.
    """
    from config.settings import settings
    if getattr(settings, "is_production", False):
        raise HTTPException(404, detail="E2E publish routes are disabled in production")

    identity = getattr(request.state, "auth_identity", None)
    from core.tenant_guard import assert_workspace_access
    try:
        workspace_id = assert_workspace_access(identity, body.workspace_id)
    except Exception as exc:
        raise HTTPException(403, detail=str(exc)) from exc

    container = getattr(request.app.state, "container", None)

    if body.dry_run:
        from services.publish_orchestration import run_publish
        return await run_publish(
            container=container,
            workspace_id=workspace_id,
            brand_id=body.brand_id,
            platform=body.platform,
            topic=body.topic,
            caption=body.caption,
            dry_run=True,
            source="api_e2e",
            risk_level=body.risk_level,
            durable=False,
        )

    from services.publish_orchestration import execute_governed_publish
    from core.decision_gate import DecisionGateError
    try:
        return await execute_governed_publish(
            container=container,
            workspace_id=workspace_id,
            brand_id=body.brand_id,
            platform=body.platform,
            topic=body.topic or "",
            caption=body.caption or "",
            source="api_e2e",
            risk_level=body.risk_level,
            decision_id=getattr(body, "decision_id", None),
            approved=False,
            dry_run=False,
        )
    except DecisionGateError as exc:
        raise HTTPException(403, detail=str(exc)) from exc


@router.get("/e2e/director-hands")
async def director_hands(
    request: Request,
    _auth: bool = Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    """Diagnostic-only Director binding inspection for controlled E2E environments."""
    from config.settings import settings
    if getattr(settings, "is_production", False):
        raise HTTPException(404, detail="E2E diagnostic routes are disabled in production")
    identity = getattr(request.state, "auth_identity", None)
    if not identity or not getattr(identity, "is_species", False):
        raise HTTPException(403, detail="Species operator required")
    container = getattr(request.app.state, "container", None)
    director = None
    if container:
        try:
            from services.director import Director
            director = container.try_resolve(Director)
        except Exception:
            pass
    if director is None:
        return {"ok": False, "error": "Director not in container"}
    caps = {}
    for name, c in getattr(director, "capabilities", {}).items():
        h = getattr(c, "handler", None)
        hname = getattr(h, "__name__", str(h))
        caps[name] = {
            "handler": hname[:100],
            "is_placeholder": "delegated" in hname or hname == "<lambda>",
            "authority": str(getattr(c, "authority", "")),
            "risk": str(getattr(c, "risk", "")),
        }
    placeholders = sum(1 for v in caps.values() if v["is_placeholder"])
    return {
        "ok": True,
        "total": len(caps),
        "placeholders_remaining": placeholders,
        "capabilities": caps,
    }
