"""KCPG-facing Next.js integration contract over the canonical Website Connector."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Request
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




async def require_website_connector(
    request: Request,
    integration_id: str,
    authorization: Optional[str] = Header(None),
) -> RequestContext:
    """Authenticate a website connector token for exactly one integration."""
    raw_token = ""
    if authorization and authorization.lower().startswith("bearer "):
        raw_token = authorization.split(" ", 1)[1].strip()
    if not raw_token:
        raise HTTPException(401, "Website connector bearer token required")
    try:
        from core.api_tokens import api_token_manager
        record = await asyncio.to_thread(api_token_manager.validate_token, raw_token)
    except Exception as exc:
        logger.warning("Website connector authentication unavailable: %s", type(exc).__name__)
        raise HTTPException(503, "Website connector authentication unavailable") from exc
    if not record:
        raise HTTPException(401, "Invalid or revoked website connector credential")
    if record.get("credential_purpose") != "website_connector":
        raise HTTPException(403, "Website connector credential purpose required")
    constraints = record.get("resource_constraints")
    if (
        not isinstance(constraints, dict)
        or set(constraints) != {"integration_id"}
        or constraints.get("integration_id") != integration_id
    ):
        raise HTTPException(403, "Credential is not authorized for this website integration")
    permissions = tuple(record.get("permissions") or ())
    if "write" not in permissions:
        raise HTTPException(403, "Website event write permission required")
    workspace_id = str(record.get("workspace_id") or "").strip()
    if not workspace_id:
        raise HTTPException(403, "Website connector credential has no workspace scope")
    ctx = RequestContext(
        principal_id=str(record.get("id") or "website-connector"),
        workspace_id=workspace_id,
        role="operator",
        permissions=permissions,
        principal_type="machine",
        auth_method="website_connector_token",
        credential_purpose="website_connector",
        resource_constraints=constraints,
    )
    request.state.request_context = ctx
    return ctx


class WebsiteConnectorCredentialResponse(BaseModel):
    key_id: str
    token: str
    credential_purpose: str
    integration_id: str
    expires_at: str
    note: str = "Store this token securely. It is shown only once."


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


@router.post("/{integration_id}/credentials", dependencies=[Depends(require_human_workspace_admin)])
async def issue_connector_credential(
    integration_id: str,
    request: Request,
    ctx: RequestContext = Depends(require_auth_context),
):
    """Issue a 90-day, single-integration website connector credential."""
    service = _application_service(request)
    integration = service.get(WebsiteIntegrationContext(ctx.workspace_id), integration_id)
    if integration is None:
        raise HTTPException(404, "Website integration not found")
    integration_status = getattr(
        getattr(integration, "status", None), "value",
        getattr(integration, "status", None),
    )
    if integration_status not in ("configured", "connected"):
        raise HTTPException(409, "Website integration must be configured or connected")
    expires_at = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    try:
        from core.api_tokens import api_token_manager
        result = await asyncio.to_thread(
            api_token_manager.create_website_connector_token,
            ctx.workspace_id,
            integration_id,
            expires_at=expires_at,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        logger.error("Website connector credential persistence failed: %s", type(exc).__name__)
        raise HTTPException(503, "Credential could not be durably stored") from exc
    logger.info(
        "website_connector.credential.issued workspace=%s integration=%s key_id=%s",
        ctx.workspace_id, integration_id, result["id"],
    )
    return {
        "ok": True,
        "credential": WebsiteConnectorCredentialResponse(
            key_id=result["id"],
            token=result["token"],
            credential_purpose="website_connector",
            integration_id=integration_id,
            expires_at=expires_at,
        ).model_dump(),
    }


@router.delete("/{integration_id}/credentials/{credential_id}", dependencies=[Depends(require_human_workspace_admin)])
async def revoke_connector_credential(
    integration_id: str,
    credential_id: str,
    request: Request,
    ctx: RequestContext = Depends(require_auth_context),
):
    """Revoke only a connector credential belonging to this integration/workspace."""
    integration = _application_service(request).get(
        WebsiteIntegrationContext(ctx.workspace_id), integration_id
    )
    if integration is None:
        raise HTTPException(404, "Website integration not found")
    from core.api_tokens import api_token_manager
    records = await asyncio.to_thread(api_token_manager.list_tokens, ctx.workspace_id)
    target = next(
        (
            item for item in records
            if item.get("id") == credential_id
            and item.get("credential_purpose") == "website_connector"
            and (item.get("resource_constraints") or {}).get("integration_id") == integration_id
            and not item.get("revoked", False)
        ),
        None,
    )
    if target is None:
        raise HTTPException(404, "Active website connector credential not found")
    revoked = await asyncio.to_thread(
        api_token_manager.revoke_token, credential_id, ctx.workspace_id
    )
    if not revoked:
        raise HTTPException(503, "Credential revocation could not be persisted")
    logger.info(
        "website_connector.credential.revoked workspace=%s integration=%s key_id=%s",
        ctx.workspace_id, integration_id, credential_id,
    )
    return {"ok": True, "revoked": True, "key_id": credential_id}


@router.get("/{integration_id}", dependencies=[Depends(require_workspace_operator)])
async def get_integration(integration_id: str, request: Request, ctx: RequestContext = Depends(require_auth_context)):
    item = _application_service(request).get(WebsiteIntegrationContext(ctx.workspace_id), integration_id)
    if item is None:
        raise HTTPException(404, "Website integration not found")
    return {"ok": True, "integration": item.model_dump(mode="json")}


@router.post("/{integration_id}/events")
async def receive_event(
    integration_id: str,
    body: WebsiteEventEnvelope,
    request: Request,
    ctx: RequestContext = Depends(require_website_connector),
):
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
