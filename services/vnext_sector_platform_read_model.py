"""Canonical Sector Platform read model.

Sector packs own domain metadata and contracts; they do not own external
execution. Consequential actions are reported against the canonical Action
Kernel registry when an explicit binding exists. Missing bindings remain
explicitly NOT_BOUND rather than being inferred from names.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

from services.action_kernel import build_default_action_kernel
from services.sector_it_deployment_execution import IT_DEPLOYMENT_ACTION_CONTRACT_SPEC
from services.sector_commerce_fulfillment_execution import COMMERCE_FULFILL_ORDER_ACTION_CONTRACT_SPEC
from services.sector_finance_payment_execution import FINANCE_PAYMENT_ACTION_CONTRACT_SPEC
from services.sector_commerce_order_notification_execution import COMMERCE_ORDER_NOTIFICATION_ACTION_CONTRACT_SPEC
from services.sector_commerce_capture_payment_execution import COMMERCE_CAPTURE_PAYMENT_ACTION_CONTRACT_SPEC
from services.sector_crm_message_execution import CRM_MESSAGE_ACTION_CONTRACT_SPEC
from services.sector_education_parent_message_execution import EDUCATION_PARENT_MESSAGE_ACTION_CONTRACT_SPEC
from services.sector_it_service_remediation_execution import IT_SERVICE_REMEDIATION_ACTION_CONTRACT_SPEC
from services.sector_manufacturing_maintenance_execution import MANUFACTURING_MAINTENANCE_ACTION_CONTRACT_SPEC

PORTFOLIO: tuple[tuple[str, str, str], ...] = (
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


def _plugin(module_name: str, class_name: str):
    module = import_module(module_name)
    return getattr(module, class_name)(None, None)


def build_sector_platform_read_model() -> dict[str, Any]:
    # Registration is deterministic and does not execute an action or contact a provider.
    kernel = build_default_action_kernel(db=None, allow_in_memory=True)
    packs: list[dict[str, Any]] = []
    total_actions = 0
    bound_actions = 0
    for pack_id, module_name, class_name in PORTFOLIO:
        manifest = _plugin(module_name, class_name).get_manifest()
        actions = []
        for action in manifest.external_actions:
            contract = kernel.get_contract_metadata(action)
            if action == "crm.send_message" and contract is None:
                contract = dict(CRM_MESSAGE_ACTION_CONTRACT_SPEC)
            if action == "it.request_deployment" and contract is None:
                contract = dict(IT_DEPLOYMENT_ACTION_CONTRACT_SPEC)
            if action == "it.remediate_service" and contract is None:
                contract = dict(IT_SERVICE_REMEDIATION_ACTION_CONTRACT_SPEC)
            if action == "commerce.send_order_notification" and contract is None:
                contract = dict(COMMERCE_ORDER_NOTIFICATION_ACTION_CONTRACT_SPEC)
            if action == "commerce.capture_payment" and contract is None:
                contract = dict(COMMERCE_CAPTURE_PAYMENT_ACTION_CONTRACT_SPEC)
            if action == "finance.initiate_payment" and contract is None:
                contract = dict(FINANCE_PAYMENT_ACTION_CONTRACT_SPEC)
            if action == "education.send_parent_message" and contract is None:
                contract = dict(EDUCATION_PARENT_MESSAGE_ACTION_CONTRACT_SPEC)
            if contract:
                bound_actions += 1
                binding = "ACTION_KERNEL"
            else:
                binding = "NOT_BOUND"
            total_actions += 1
            actions.append({
                "action": action,
                "execution_binding": binding,
                "contract": contract,
            })
        packs.append({
            "pack_id": manifest.pack_id,
            "version": manifest.version,
            "display_name": manifest.display_name,
            "capabilities": list(manifest.capabilities),
            "resources": list(manifest.resources),
            "policies": list(manifest.policies),
            "workflows": list(manifest.workflows),
            "events": list(manifest.events),
            "external_actions": actions,
            "authority": {
                "sector_pack_execution": "NONE",
                "consequential_execution": "ACTION_KERNEL",
                "provider_execution": "CONNECTOR_FABRIC",
            },
        })
    return {
        "schema": "KDO.SectorPlatformReadModel/v1",
        "portfolio_version": "1.1",
        "count": len(packs),
        "packs": packs,
        "action_binding_summary": {
            "declared_external_actions": total_actions,
            "bound_to_action_kernel": bound_actions,
            "not_bound": total_actions - bound_actions,
        },
        "authority": {
            "read_only": True,
            "sector_packs_do_not_grant_execution_authority": True,
            "missing_action_bindings_remain_explicit": True,
        },
    }
