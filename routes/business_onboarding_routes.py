"""V1.4 backend onboarding HTTP adapter.

Transport concerns stay here; application orchestration and persistence are
owned by the application service and its infrastructure adapter.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from application.business_onboarding import BusinessOnboardingApplicationService, OnboardingContext
from services.business_onboarding_adapter import BusinessOnboardingServiceAdapter

router = APIRouter(prefix="/api/v1/onboarding", tags=["business-onboarding"])


def _auth_dep():
    from api.v1 import require_auth
    return require_auth


def _workspace_id() -> str:
    from core.request_context import require_request_context
    ctx = require_request_context()
    if not ctx.workspace_id:
        raise HTTPException(403, detail="Authenticated request has no workspace scope")
    return str(ctx.workspace_id)


def _application_service(request: Request) -> BusinessOnboardingApplicationService:
    factory = getattr(request.app.state, "business_onboarding_application_factory", None)
    if factory is not None:
        return factory()
    container = getattr(request.app.state, "container", None)
    db = getattr(getattr(container, "supabase_service", None), "client", None) if container else None
    return BusinessOnboardingApplicationService(BusinessOnboardingServiceAdapter(db))


class DescribeRequest(BaseModel):
    description: str = Field(min_length=10, max_length=10000)


class ProvisionRequest(BaseModel):
    plan: dict
    activate: bool = False


@router.post("/understand", dependencies=[Depends(_auth_dep())])
async def understand(payload: DescribeRequest, request: Request):
    result = await _application_service(request).understand(payload.description)
    return result.__dict__


@router.post("/plan", dependencies=[Depends(_auth_dep())])
async def plan(payload: DescribeRequest, request: Request):
    try:
        return await _application_service(request).plan(
            OnboardingContext(_workspace_id()), payload.description
        )
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/provision", dependencies=[Depends(_auth_dep())])
async def provision(payload: ProvisionRequest, request: Request):
    try:
        return _application_service(request).provision(
            OnboardingContext(_workspace_id()), payload.plan, activate=payload.activate
        )
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, detail=f"business_onboarding_unavailable:{str(exc)[:180]}") from exc
