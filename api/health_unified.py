from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/unified")
async def unified_health():
    from services.unified_health import collect_unified_health
    return await collect_unified_health()
