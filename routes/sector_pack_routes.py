"""Read-only sector-pack portfolio surfaces for the KDO control plane.

Sector packs declare domain metadata. They do not become an execution plane.
Consequential external actions remain governed by the canonical Action Kernel.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(prefix="/api/v1/sector-packs", tags=["sector-packs"])

# Canonical v1.1 portfolio. These are the sector packs certified by the
# backend architecture suite; legacy/special-purpose domain plugins are not
# silently promoted into the enterprise portfolio.
_PORTFOLIO: tuple[tuple[str, str, str], ...] = (
    ("marketing_media", "plugins.industries.marketing_media.plugin", "MarketingMediaPlugin"),
    ("commerce", "plugins.industries.commerce.plugin", "CommercePlugin"),
    ("it_operations", "plugins.industries.it_operations.plugin", "ITOperationsPlugin"),
    ("cybersecurity", "plugins.industries.cybersecurity.plugin", "CybersecurityPlugin"),
    ("crm", "plugins.industries.crm.plugin", "CRMPlugin"),
    ("education", "plugins.industries.education.plugin", "EducationPlugin"),
    ("healthcare", "plugins.industries.healthcare.plugin", "HealthcarePlugin"),
    ("finance", "plugins.industries.finance.plugin", "FinancePlugin"),
    ("supply_chain", "plugins.industries.supply_chain.plugin", "SupplyChainPlugin"),
    ("manufacturing", "plugins.industries.manufacturing.plugin", "ManufacturingPlugin"),
    ("energy", "plugins.industries.energy.plugin", "EnergyPlugin"),
    ("government", "plugins.industries.government.plugin", "GovernmentPlugin"),
    ("agriculture", "plugins.industries.agriculture.plugin", "AgriculturePlugin"),
)


def _auth_dep():
    from api.v1 import require_auth
    return require_auth


def _load(pack_id: str) -> dict[str, Any]:
    for expected_id, module_name, class_name in _PORTFOLIO:
        if expected_id != pack_id:
            continue
        try:
            module = import_module(module_name)
            plugin = getattr(module, class_name)(None, None)
            manifest = plugin.get_manifest()
            return {
                "pack_id": manifest.pack_id,
                "version": manifest.version,
                "display_name": manifest.display_name,
                "capabilities": list(manifest.capabilities),
                "resources": list(manifest.resources),
                "policies": list(manifest.policies),
                "workflows": list(manifest.workflows),
                "events": list(manifest.events),
                "webhooks": list(manifest.webhooks),
                "external_actions": list(manifest.external_actions),
                "authority": {
                    "metadata_only": True,
                    "execution_authority": "ACTION_KERNEL",
                    "provider_execution": "CONNECTOR_FABRIC",
                },
            }
        except Exception as exc:
            raise HTTPException(503, detail=f"sector_pack_unavailable:{pack_id}:{type(exc).__name__}") from exc
    raise HTTPException(404, detail="sector pack not found")


@router.get("", dependencies=[Depends(_auth_dep())])
async def list_sector_packs() -> dict[str, Any]:
    """Return the canonical sector-pack portfolio as backend-owned metadata."""
    packs = [_load(pack_id) for pack_id, _module, _class in _PORTFOLIO]
    return {
        "schema": "KDO.SectorPackPortfolio/v1",
        "version": "1.1",
        "count": len(packs),
        "packs": packs,
        "authority": {
            "read_only": True,
            "sector_packs_do_not_grant_execution_authority": True,
            "consequential_actions_must_use": ["GOVERNANCE", "ACTION_KERNEL", "CONNECTOR_FABRIC"],
        },
    }




@router.get("/control-plane", dependencies=[Depends(_auth_dep())])
async def sector_platform_control_plane() -> dict[str, Any]:
    """Return sector manifests plus explicit Action Kernel binding state."""
    from services.vnext_sector_platform_read_model import build_sector_platform_read_model
    return build_sector_platform_read_model()


@router.get("/action-contracts", dependencies=[Depends(_auth_dep())])
async def sector_action_contracts() -> dict[str, Any]:
    """Return the canonical sector action-contract registry as read-only evidence."""
    from services.sector_action_contract_registry import build_sector_action_contract_registry
    return build_sector_action_contract_registry()

@router.get("/governance-readiness", dependencies=[Depends(_auth_dep())])
async def sector_action_governance_readiness() -> dict[str, Any]:
    """Return read-only evidence for the remaining sector-action registration gates."""
    from services.sector_action_governance_readiness import build_sector_action_governance_readiness
    return build_sector_action_governance_readiness()


@router.get("/implementation-gates", dependencies=[Depends(_auth_dep())])
async def sector_action_implementation_gates() -> dict[str, Any]:
    """Return explicit read-only implementation gates for sector actions."""
    from services.sector_action_implementation_gate import build_sector_action_implementation_gate
    return build_sector_action_implementation_gate()


@router.get("/it-operations/deployments/{deployment_id}/reconciliation-evidence", dependencies=[Depends(_auth_dep())])
async def it_deployment_reconciliation_evidence(deployment_id: str, workspace_id: str):
    """Return read-only durable reconciliation evidence for an IT deployment."""
    from plugins.industries.it_operations.repository import ITOperationsRepository
    from services.it_deployment_evidence_read_model import build_it_deployment_evidence
    try:
        return build_it_deployment_evidence(ITOperationsRepository(), workspace_id=workspace_id, deployment_id=deployment_id)
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.get("/{pack_id}", dependencies=[Depends(_auth_dep())])
async def get_sector_pack(pack_id: str) -> dict[str, Any]:
    """Return one canonical sector-pack manifest without executing domain commands."""
    return _load(pack_id)

@router.get("/provider-gap-requirements", dependencies=[Depends(_auth_dep())])
def sector_provider_gap_requirements():
    """Return explicit, read-only requirements for unresolved provider semantics."""
    from services.sector_provider_gap_requirements import build_sector_provider_gap_requirements
    return build_sector_provider_gap_requirements()


@router.get("/provider-binding-contracts")
def sector_provider_binding_contracts():
    """Read-only semantic contracts required to close provider binding gaps."""
    from services.sector_provider_binding_contracts import build_sector_provider_binding_contracts
    return build_sector_provider_binding_contracts()
