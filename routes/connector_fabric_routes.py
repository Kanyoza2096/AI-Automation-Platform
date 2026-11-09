"""Connector Fabric control-plane and explicit observe-only health surfaces.

The catalog/evidence GET routes remain read-only. The authenticated health
observation POST performs one account-bound provider health check, persists
its evidence and publishes a signed event. It never executes connector actions.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/connectors/fabric", tags=["connector-fabric"])


def _auth_dep():
    from api.v1 import require_auth
    return require_auth


def _workspace_id(request: Request) -> str:
    # Reuse the canonical Command Center workspace derivation rather than
    # trusting a workspace_id supplied by the caller.
    from routes.command_center_routes import _workspace_id as command_center_workspace_id
    return str(command_center_workspace_id(request))


@router.get("", dependencies=[Depends(_auth_dep())])
async def connector_fabric_read_model(request: Request):
    """Canonical Provider -> Adapter -> Contract catalog; read-only."""
    from services.vnext_connector_fabric_read_model import build_connector_fabric_read_model
    return build_connector_fabric_read_model(_workspace_id(request))


@router.get("/{connector_key}/evidence", dependencies=[Depends(_auth_dep())])
async def connector_fabric_evidence(connector_key: str, request: Request):
    """Durable health/certification evidence; never execution authority."""
    from services.vnext_connector_fabric_evidence_read_model import (
        EvidenceReadError,
        build_connector_fabric_evidence,
    )
    try:
        return build_connector_fabric_evidence(_workspace_id(request), connector_key)
    except KeyError as exc:
        raise HTTPException(404, detail="Connector not found") from exc
    except EvidenceReadError as exc:
        raise HTTPException(503, detail="Connector evidence is temporarily unavailable") from exc
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.get("/evidence-coverage", dependencies=[Depends(_auth_dep())])
async def connector_fabric_evidence_coverage(request: Request):
    """Portfolio receipt/verification/reconciliation evidence coverage; read-only."""
    from services.vnext_connector_fabric_read_model import connector_outcome_evidence_coverage
    payload = connector_outcome_evidence_coverage()
    payload["workspaceId"] = _workspace_id(request)
    return payload

@router.get("/{connector_key}", dependencies=[Depends(_auth_dep())])
async def connector_fabric_detail(connector_key: str, request: Request):
    """Read-only evidence projection for one connector."""
    from services.vnext_connector_fabric_read_model import get_connector_fabric_record
    try:
        return get_connector_fabric_record(_workspace_id(request), connector_key)
    except KeyError as exc:
        raise HTTPException(404, detail="Connector not found") from exc
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc



class FacebookHealthObservationRequest(BaseModel):
    brand_id: str = Field(min_length=1, max_length=160)
    connection_id: str = Field(min_length=1, max_length=160)


@router.post("/facebook/health-observations", dependencies=[Depends(_auth_dep())])
async def observe_facebook_health(
    payload: FacebookHealthObservationRequest,
    request: Request,
):
    """Run one authenticated, read-only Facebook health probe for a tenant."""
    from api.support import auth_identity_var
    from services.v69_tenant_facebook_health_context import (
        TenantFacebookHealthContextError,
    )
    from services.v69_tenant_facebook_health_observation_service import (
        TenantFacebookHealthObservationService,
    )
    from services.v69_tenant_facebook_health_producer import TenantHealthPublisherError

    identity = auth_identity_var.get()
    if not identity or not getattr(identity, "valid", False):
        raise HTTPException(401, detail="Authenticated identity required")
    workspace_id = _workspace_id(request)
    if str(getattr(identity, "workspace_id", "") or "") != workspace_id:
        raise HTTPException(403, detail="Authenticated workspace mismatch")
    if not getattr(identity, "has_permission", lambda _permission: False)("write"):
        raise HTTPException(403, detail="Connector health observation permission required")

    runtime = getattr(request.app.state, "tenant_facebook_provenance_runtime", None)
    if runtime is None or not getattr(runtime, "health_evidence_ready", False):
        raise HTTPException(
            503,
            detail="Tenant Facebook health observation is not enabled in this environment",
        )

    client = getattr(getattr(runtime, "event_bus", None), "supabase", None)
    if client is None:
        raise HTTPException(503, detail="Tenant health evidence store unavailable")

    try:
        from services.facebook import facebook

        service = TenantFacebookHealthObservationService(
            provenance_runtime=runtime,
            supabase_client=client,
            facebook_service=facebook,
        )
        result = await service.observe(
            identity,
            brand_id=payload.brand_id,
            connection_id=payload.connection_id,
        )
    except TenantFacebookHealthContextError as exc:
        raise HTTPException(
            409,
            detail="Facebook connection is not eligible for a tenant health observation",
        ) from exc
    except TenantHealthPublisherError as exc:
        if exc.receipt is not None:
            raise HTTPException(
                503,
                detail={
                    "error": "health_event_publication_unconfirmed",
                    "evidence_id": exc.receipt.evidence_id,
                    "observation_id": exc.receipt.observation_id,
                    "health_state": exc.receipt.health_state,
                    "message": "Durable evidence was retained; reconcile it without repeating the provider probe.",
                },
            ) from exc
        raise HTTPException(
            503,
            detail="Tenant Facebook health observation could not be completed safely",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            503,
            detail="Tenant Facebook health observation is temporarily unavailable",
        ) from exc

    return {
        "ok": True,
        "workspace_id": result.workspace_id,
        "brand_id": result.brand_id,
        "connection_id": result.connection_id,
        "social_account_id": result.social_account_id,
        "health_state": result.health_state,
        "evidence_id": result.evidence_id,
        "observation_id": result.observation_id,
        "event_id": result.event_id,
        "authority": "none",
        "operation": "observe_only",
    }
