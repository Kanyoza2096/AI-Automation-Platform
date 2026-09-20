from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, HttpUrl

from core.security.deps import require_auth_context, require_workspace, require_workspace_admin
from domain.affiliate.attribution import verify_webhook
from services.affiliate_service import AffiliateService

public_router = APIRouter(prefix="/api/v1/affiliate", tags=["affiliate-public"])
router = APIRouter(prefix="/api/v1/affiliate", tags=["affiliate"], dependencies=[Depends(require_auth_context), Depends(require_workspace_admin)])


class ProductCreate(BaseModel):
    name: str
    provider: str
    program: str
    destination_domains: list[str] = Field(min_length=1)
    commission_rate: float = Field(default=0, ge=0, le=1)
    currency: str = "USD"
    metadata: dict[str, Any] = Field(default_factory=dict)


class LinkCreate(BaseModel):
    product_id: str
    destination_url: HttpUrl
    slug: Optional[str] = None
    campaign: str = ""
    content_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionRequest(BaseModel):
    action_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "low"
    decision_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    approved: bool = False


def _db():
    from services.supabase import supabase_service
    db = supabase_service.service_client or supabase_service.client
    if db is None:
        raise HTTPException(status_code=503, detail="affiliate database unavailable")
    return db


@public_router.get("/go/{slug}", include_in_schema=False)
async def affiliate_redirect(slug: str, request: Request):
    service = AffiliateService(_db())
    try:
        link = service.resolve_redirect(slug)
        service.record_click(link_id=link["id"], metadata={"user_agent": request.headers.get("user-agent", "")[:300]})
    except LookupError:
        raise HTTPException(status_code=404, detail="affiliate link not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(status_code=307, headers={"Location": link["destination_url"], "Cache-Control": "no-store"})


@public_router.post("/webhooks/{provider}", include_in_schema=False)
async def affiliate_webhook(provider: str, request: Request, x_affiliate_signature: str = Header("")):
    body = await request.body()
    secret = os.getenv(f"AFFILIATE_{provider.upper()}_WEBHOOK_SECRET") or os.getenv("AFFILIATE_WEBHOOK_SECRET")
    if not secret or not verify_webhook(body, x_affiliate_signature, secret):
        raise HTTPException(status_code=401, detail="invalid affiliate webhook signature")
    try:
        payload = await request.json()
        service = AffiliateService(_db())
        required = ("product_id", "external_order_id", "amount", "commission", "currency", "status")
        if any(k not in payload for k in required):
            raise ValueError("affiliate conversion payload missing required fields")
        return service.ingest_conversion(provider=provider, metadata=payload.get("metadata"), **{k: payload[k] for k in required}, event_id=payload.get("event_id"), occurred_at=payload.get("occurred_at"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/products")
async def create_product(payload: ProductCreate, workspace_id: str = Depends(require_workspace), _admin=Depends(require_workspace_admin)):
    return {"ok": True, "product": AffiliateService(_db()).create_product(workspace_id=workspace_id, **payload.model_dump())}


@router.post("/links")
async def create_link(payload: LinkCreate, workspace_id: str = Depends(require_workspace), _admin=Depends(require_workspace_admin)):
    data = payload.model_dump()
    data["destination_url"] = str(data["destination_url"])
    return {"ok": True, "link": AffiliateService(_db()).create_link(workspace_id=workspace_id, **data)}


@router.get("/analytics")
async def analytics(days: int = 30, workspace_id: str = Depends(require_workspace)):
    return AffiliateService(_db()).analytics(workspace_id=workspace_id, days=days)


@router.post("/actions")
async def action(payload: ActionRequest, workspace_id: str = Depends(require_workspace), _admin=Depends(require_workspace_admin)):
    from services.action_kernel import build_default_action_kernel
    kernel = build_default_action_kernel(_db())
    return await kernel.enqueue(workspace_id=workspace_id, action_type=payload.action_type, parameters=payload.parameters,
                                risk_level=payload.risk_level, decision_id=payload.decision_id,
                                idempotency_key=payload.idempotency_key, source="affiliate_route", approved=payload.approved)
