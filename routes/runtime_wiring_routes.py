"""V71.14 runtime route/event wiring observability."""
from fastapi import APIRouter, Depends, HTTPException, Request

router = APIRouter(prefix="/api/v1/runtime", tags=["runtime-convergence"])


def _auth():
    from api.v1 import require_auth
    return require_auth


@router.get("/route-event-wiring", dependencies=[Depends(_auth())])
async def route_event_wiring(request: Request):
    wiring = getattr(request.app.state, "route_event_wiring", None)
    if wiring is None:
        raise HTTPException(503, detail="V71.14 route/event wiring is not initialized")
    return wiring.status()
