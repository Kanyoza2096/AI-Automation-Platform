from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1/runtime", tags=["runtime"])

@router.get("/legacy-authority-fence")
async def legacy_authority_fence(request: Request):
    fence = getattr(request.app.state, "legacy_runtime_authority_fence", None)
    if fence is None:
        return {"status": "not_installed", "canonical": False}
    return fence.status()
