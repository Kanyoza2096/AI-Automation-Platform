"""Read-only sensor health/status surfaces."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from core.security.deps import require_auth_context

router = APIRouter(prefix="/api/v1/sensors", tags=["sensors"], dependencies=[Depends(require_auth_context)])


@router.get("/status")
async def sensors_status():
    # The generic sensor endpoint remains available for future registered sensors.
    return {"ok": True, "sensors": {}}
