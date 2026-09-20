"""V71.16 runtime visibility route."""
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1/runtime", tags=["runtime"])

@router.get("/autonomous-workforce-bridge")
async def autonomous_workforce_bridge(request: Request):
    bridge = getattr(request.app.state, "autonomous_loop_workforce_bridge", None)
    if bridge is None:
        return {"ok": False, "status": "not_installed", "version": "v71.16"}
    return {"ok": True, **bridge.status()}
