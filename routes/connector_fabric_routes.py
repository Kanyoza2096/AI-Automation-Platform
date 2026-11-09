"""Read-only Connector Fabric control-plane surfaces.

These routes expose backend-owned connector architecture and evidence only.
They do not instantiate providers, mutate registry state, execute actions, or
turn health/certification evidence into authority.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

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
