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


@router.post("/e2e/publish")
async def e2e_publish(
    body: PublishBody,
    request: Request,
    _auth: bool = Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    """
    Run the real publish orchestration path.

    dry_run=true (default): decision + caption generation + plugin path without Graph API.
    dry_run=false: requires live social tokens — will post for real.
    """
    identity = getattr(request.state, "auth_identity", None)
    from core.tenant_guard import assert_workspace_access
    try:
        workspace_id = assert_workspace_access(identity, body.workspace_id)
    except Exception as exc:
        raise HTTPException(403, detail=str(exc)) from exc

    container = getattr(request.app.state, "container", None)
    from services.publish_orchestration import run_publish
    result = await run_publish(
        container=container,
        workspace_id=workspace_id,
        brand_id=body.brand_id,
        platform=body.platform,
        topic=body.topic,
        caption=body.caption,
        dry_run=body.dry_run,
        source="api_e2e",
        risk_level=body.risk_level,
        durable=body.durable,
    )
    return result


@router.get("/e2e/director-hands")
async def director_hands(
    request: Request,
    _auth: bool = Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    """Show whether Director capabilities are bound or still placeholders."""
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
