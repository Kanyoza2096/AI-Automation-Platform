"""V71.17 durable workforce execution control surface."""
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1/runtime", tags=["runtime"])

@router.get("/workforce-execution")
async def workforce_execution_status(request: Request):
    factory = getattr(request.app.state, "workforce_execution_coordinator_factory", None)
    if factory is None:
        return {"ok": False, "status": "not_installed", "version": "v71.17"}
    return {
        "ok": True,
        "version": "v71.17",
        "installed": True,
        "workspace_required_for_execution": True,
        "execution_authority": "existing Action Kernel only",
        "parallel_execution_plane": False,
    }
