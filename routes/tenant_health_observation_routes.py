"""Authenticated, observe-only tenant Facebook health operation.

This route accepts only tenant resource identifiers. Workspace identity,
credentials, connector state and certification are resolved server-side.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

router = APIRouter(prefix="/api/v1/tenant-health", tags=["tenant-health"])


def _auth_dep():
    from api.v1 import require_auth
    return require_auth


@router.post(
    "/facebook/connections/{connection_id}/observe",
    dependencies=[Depends(_auth_dep())],
)
async def observe_tenant_facebook_health(
    request: Request,
    connection_id: str = Path(..., min_length=1, max_length=160),
    brand_id: str = Query(..., min_length=1, max_length=160),
):
    """Run one bounded read-only Page health probe for the authenticated tenant."""
    from api.support import auth_identity_var
    from services.v69_tenant_facebook_health_context import TenantFacebookHealthContextError
    from services.v69_tenant_facebook_health_producer import TenantHealthPublisherError
    from services.v69_tenant_facebook_health_service import TenantFacebookHealthServiceError

    identity = auth_identity_var.get()
    if identity is None or not getattr(identity, "valid", False):
        raise HTTPException(status_code=401, detail="Authentication required")
    workspace_id = str(getattr(identity, "workspace_id", "") or "").strip()
    if not workspace_id:
        # Platform/species credentials must choose a tenant through a separate,
        # explicitly governed workspace-assumption flow. This route never does.
        raise HTTPException(status_code=403, detail="Workspace-scoped credential required")
    permission_check = getattr(identity, "has_permission", None)
    if not callable(permission_check) or not permission_check("write"):
        raise HTTPException(status_code=403, detail="Connector health observation permission required")

    service = getattr(request.app.state, "tenant_facebook_health_service", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Tenant Facebook health observation is not enabled in this environment",
        )

    try:
        publication = await service.observe(
            identity,
            brand_id=brand_id,
            connection_id=connection_id,
        )
    except TenantFacebookHealthContextError as exc:
        code = str(exc)
        if code == "social_account_access_permission_required":
            raise HTTPException(status_code=403, detail="Workspace account access denied") from exc
        if code.endswith("_not_found"):
            raise HTTPException(status_code=404, detail="Tenant connector context not found") from exc
        raise HTTPException(status_code=503, detail="Tenant connector context is unavailable") from exc
    except TenantFacebookHealthServiceError as exc:
        code = str(exc)
        if code == "health_observation_cooldown_or_in_progress":
            raise HTTPException(
                status_code=429,
                detail="A health observation was already started in the current five-minute window",
            ) from exc
        if code.startswith("health_observation_ledger_"):
            raise HTTPException(status_code=503, detail="Health observation ledger is unavailable") from exc
        raise HTTPException(status_code=409, detail="Tenant health observation could not be admitted") from exc
    except TenantHealthPublisherError as exc:
        receipt = exc.receipt
        if receipt is not None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "health_evidence_persisted_event_pending",
                    "evidence_id": receipt.evidence_id,
                    "observation_id": receipt.observation_id,
                    "health_state": receipt.health_state,
                },
            ) from exc
        raise HTTPException(status_code=503, detail="Tenant health observation failed") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Tenant health observation is unavailable") from exc

    return {
        "ok": True,
        "workspace_id": publication.workspace_id,
        "brand_id": publication.brand_id,
        "connection_id": publication.connection_id,
        "health_state": publication.receipt.health_state,
        "evidence_id": publication.receipt.evidence_id,
        "observation_id": publication.receipt.observation_id,
        "event_id": publication.event_id,
    }


__all__ = ["router", "observe_tenant_facebook_health"]
