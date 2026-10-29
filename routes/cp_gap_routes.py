"""Remaining Control Plane gap endpoints (thin but real HTTP surface)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from core.security.deps import require_species_privileges

logger = logging.getLogger("kanyoza.routes.cp_gaps")
router = APIRouter(
    prefix="/api/v1",
    tags=["control-plane-gaps"],
    dependencies=[Depends(require_species_privileges)],
)


class QualityBody(BaseModel):
    content: str = Field(default="", max_length=20000)


# ═══════════════════════════════════════════════════════════════════════════
# EXISTING ROUTES
# Truth policy: never invent "nominal" / fixed topology / heuristic scores.
# Prefer explicit unavailable + source: "none" until a real engine is bound.
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/alerts")
async def alerts():
    try:
        from services.meta_engine import meta_engine
        return {"ok": True, "alerts": meta_engine.get_alerts(), "source": "meta_engine"}
    except Exception as exc:
        return {"ok": False, "alerts": [], "source": "unavailable", "error": type(exc).__name__}


@router.get("/rules")
async def rules():
    return {"ok": True, "rules": [], "source": "none"}




@router.get("/condition/health")
async def condition_health():
    try:
        from services import condition_engine as ce_mod
        engine = getattr(ce_mod, "autonomous_engine", None)
        if engine is not None and hasattr(engine, "get_status"):
            value = engine.get_status()
            if isinstance(value, dict):
                return {
                    "ok": True,
                    "status": str(value.get("status", "unknown")).lower(),
                    "source": "condition_engine",
                    "conditions": value.get("conditions") or value.get("active") or [],
                    **{k: v for k, v in value.items() if k not in ("status", "conditions", "active")},
                }
        if hasattr(ce_mod, "get_engine_status"):
            value = ce_mod.get_engine_status()
            if isinstance(value, dict):
                return {
                    "ok": True,
                    "status": str(value.get("status", "unknown")).lower(),
                    "source": "condition_engine",
                    "conditions": value.get("conditions") or [],
                }
        if hasattr(ce_mod, "health_check"):
            value = ce_mod.health_check()
            if isinstance(value, dict):
                return {"ok": True, "status": str(value.get("status", "unknown")).lower(), "source": "condition_engine", "conditions": []}
    except Exception as exc:
        logger.warning("condition health unbound: %s", type(exc).__name__)
    return {
        "ok": False,
        "status": "unavailable",
        "source": "none",
        "conditions": [],
        "message": "No authoritative condition health provider is bound to this compatibility surface.",
    }






@router.get("/quality/posts")
async def quality_posts():
    return {"ok": True, "posts": [], "source": "none"}




@router.post("/quality/analyze")
async def quality_analyze(body: QualityBody):
    # Heuristic length-based scoring is disabled on the control-plane surface.
    return {
        "ok": False,
        "status": "unavailable",
        "source": "none",
        "score": None,
        "message": "Quality analysis is not bound; heuristic scoring is disabled on the control-plane surface.",
    }








@router.get("/applications")
async def applications():
    return {"ok": True, "applications": [], "source": "none"}


@router.get("/repos")
async def repos():
    return {"ok": True, "repos": [], "source": "none"}












# ═══════════════════════════════════════════════════════════════════════════
# FRONTEND POLLING ENDPOINTS (avoid 404s, stay truthful)
# ═══════════════════════════════════════════════════════════════════════════











def register_cp_gap_routes(app) -> None:
    app.include_router(router)
