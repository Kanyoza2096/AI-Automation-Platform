"""
Phase D — Metabolism HTTP API.

Mount under the main app:

    from api.metabolism_routes import router as metabolism_router
    app.include_router(metabolism_router, prefix="/api/v1")
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("kanyoza.api.metabolism")

router = APIRouter(prefix="/api/v1", tags=["metabolism"])


class CheckoutBody(BaseModel):
    workspace_id: str
    plan: str = Field(..., pattern=r"^(pro|business|enterprise)$")
    success_url: str
    cancel_url: str
    customer_email: Optional[str] = None


class AdminSetPlanBody(BaseModel):
    workspace_id: str
    plan: str = Field(..., pattern=r"^(free|pro|business|enterprise)$")


def _require_auth_dep():
    """Late import so routes work even if v1 not fully loaded."""
    from api.v1 import require_auth
    return require_auth


@router.get("/metabolism/plans")
async def list_plans():
    from core.metabolism import PLAN_CATALOG
    return {
        "plans": {
            name: {
                "name": p.name,
                "max_posts_per_day": p.max_posts_per_day,
                "max_ai_tokens_per_month": p.max_ai_tokens_per_month,
                "max_brands": p.max_brands,
                "max_social_accounts": p.max_social_accounts,
                "director_authority": p.director_authority,
                "industry_organs": list(p.industry_organs),
                "price_usd_monthly": p.price_usd_monthly,
            }
            for name, p in PLAN_CATALOG.items()
        }
    }


@router.get("/metabolism/status")
async def metabolism_status(
    request: Request,
    workspace_id: Optional[str] = None,
    _auth: bool = Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    from core.metabolism import metabolism
    from core.workspace_context import get_current_workspace

    # Prefer authenticated workspace
    identity = getattr(request.state, "auth_identity", None)
    try:
        from core.tenant_guard import assert_workspace_access
        requested = workspace_id or getattr(identity, "workspace_id", None) or get_current_workspace()
        ws = assert_workspace_access(identity, requested)
    except Exception as exc:
        raise HTTPException(403, detail=str(exc)) from exc
    return metabolism.status(ws)


@router.post("/metabolism/checkout")
async def create_checkout(
    body: CheckoutBody,
    request: Request,
    _auth: bool = Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
    _admin=Depends(lambda: __import__("core.security.deps", fromlist=["require_workspace_admin"]).require_workspace_admin),
):
    from core.metabolism import stripe_billing
    identity = getattr(request.state, "auth_identity", None)
    try:
        from core.tenant_guard import assert_workspace_access
        workspace_id = assert_workspace_access(identity, body.workspace_id)
    except Exception as exc:
        raise HTTPException(403, detail=str(exc)) from exc
    try:
        session = stripe_billing.create_checkout_session(
            workspace_id=workspace_id,
            plan=body.plan,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            customer_email=body.customer_email,
        )
        return session
    except Exception as exc:
        raise HTTPException(400, detail=str(exc))


@router.post("/metabolism/webhook/stripe")
async def stripe_webhook(request: Request):
    from core.metabolism import stripe_billing
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        result = stripe_billing.handle_webhook(payload, sig)
        return result
    except Exception as exc:
        logger.error("Stripe webhook error: %s", exc)
        raise HTTPException(400, detail=str(exc))


@router.post("/metabolism/admin/set-plan")
async def admin_set_plan(
    body: AdminSetPlanBody,
    request: Request,
    _auth: bool = Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    """Species operator only — set plan without Stripe (dev / enterprise)."""
    identity = getattr(request.state, "auth_identity", None)
    if not identity or not getattr(identity, "is_species", False):
        raise HTTPException(403, detail="Species operator required")
    from core.metabolism import metabolism
    try:
        from core.species_auth import validate_species_workspace
        container = getattr(request.app.state, "container", None)
        client = getattr(getattr(container, "supabase_service", None), "client", None)
        if not validate_species_workspace(body.workspace_id, client):
            raise HTTPException(403, detail="Selected workspace does not exist or is not active")
    except HTTPException:
        raise
    return metabolism.apply_plan_to_organism(body.workspace_id, body.plan)
