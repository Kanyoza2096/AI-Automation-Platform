"""V71.13 production runtime convergence observability surface."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request

router = APIRouter(prefix="/api/v1/runtime", tags=["runtime-convergence"])


def _auth():
    from api.v1 import require_auth
    return require_auth


@router.get("/convergence", dependencies=[Depends(_auth())])
async def convergence_status(request: Request):
    runtime = getattr(request.app.state, "production_runtime", None)
    if runtime is None:
        raise HTTPException(503, detail="production runtime convergence is not initialized")
    return runtime.status()


@router.get("/convergence/workspace/{workspace_id}", dependencies=[Depends(_auth())])
async def workspace_convergence_status(workspace_id: str, request: Request):
    runtime = getattr(request.app.state, "production_runtime", None)
    if runtime is None:
        raise HTTPException(503, detail="production runtime convergence is not initialized")
    identity = getattr(request.state, "auth_identity", None)
    claimed = getattr(identity, "workspace_id", None)
    if claimed and str(claimed) != str(workspace_id):
        raise HTTPException(403, detail="workspace mismatch")
    runtime.workspace_runtime(workspace_id)
    return {"workspace_id": workspace_id, "composed": True, "canonical_execution": True}
