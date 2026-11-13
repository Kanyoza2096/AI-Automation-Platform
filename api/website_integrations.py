"""KCPG-facing Next.js integration contract over the canonical Website Connector."""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core.security.deps import require_auth_context, require_human_workspace_admin, require_workspace_operator
from core.request_context import RequestContext
from contracts.website_integration import WebsiteEventEnvelope, WebsiteIntegrationRegistration
from application.website_integrations import WebsiteIntegrationApplicationService, WebsiteIntegrationContext
from api.application_dependencies import website_integration_application

logger = logging.getLogger("kanyoza.api.website_integrations")
router = APIRouter(prefix="/api/v1/website-integrations", tags=["website-integrations"])


class RegisterWebsiteRequest(WebsiteIntegrationRegistration):
    website_api_token: str = Field(min_length=1, max_length=4096)


def _application_service(request: Request) -> WebsiteIntegrationApplicationService:
    try:
        return website_integration_application(request)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("", dependencies=[Depends(require_workspace_operator)])
async def list_integrations(request: Request, ctx: RequestContext = Depends(require_auth_context)):
    return {"ok": True, "integrations": [x.model_dump(mode="json") for x in _application_service(request).list(WebsiteIntegrationContext(ctx.workspace_id))]}


@router.post("", dependencies=[Depends(require_human_workspace_admin)])
async def register_integration(body: RegisterWebsiteRequest, request: Request, ctx: RequestContext = Depends(require_auth_context)):
    service = _application_service(request)
    model = service.register(WebsiteIntegrationContext(ctx.workspace_id), body.model_copy(update={"status": "configured"}), body.website_api_token)
    return {"ok": True, "integration": model.model_dump(mode="json"), "connector": {"platform": "website", "authority": "WebsiteConnector", "executor": "canonical_action_kernel"}}


@router.get("/{integration_id}", dependencies=[Depends(require_workspace_operator)])
async def get_integration(integration_id: str, request: Request, ctx: RequestContext = Depends(require_auth_context)):
    item = _application_service(request).get(WebsiteIntegrationContext(ctx.workspace_id), integration_id)
    if item is None:
        raise HTTPException(404, "Website integration not found")
    return {"ok": True, "integration": item.model_dump(mode="json")}


@router.post("/{integration_id}/events", dependencies=[Depends(require_workspace_operator)])
async def receive_event(integration_id: str, body: WebsiteEventEnvelope, request: Request, ctx: RequestContext = Depends(require_auth_context)):
    if body.integration_id != integration_id:
        raise HTTPException(400, "integration_id mismatch")
    service = _application_service(request)
    try:
        result = await service.receive_event(WebsiteIntegrationContext(ctx.workspace_id), integration_id, body)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    return {"ok": True, **result}
