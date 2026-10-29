"""Governance-readiness evidence for declared Sector Pack actions.

This is intentionally a read-only planning/evidence layer. It does not invent
risk, approval, authorization, connector, receipt, verification, or
reconciliation semantics. Existing canonical evidence is surfaced when it is
explicitly supplied by the Action Kernel and Connector Fabric; all other
actions remain blocked pending canonical evidence.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

from services.vnext_sector_platform_read_model import PORTFOLIO
from services.sector_action_contract_registry import build_sector_action_contract_registry


def _plugin(module_name: str, class_name: str):
    module = import_module(module_name)
    return getattr(module, class_name)(None, None)


def _command_metadata(plugin: Any) -> dict[str, dict[str, Any]]:
    commands = getattr(plugin, "commands_handler", None)
    if commands is None or not callable(getattr(commands, "list_all", None)):
        return {}
    listing = commands.list_all()
    return {
        str(item.get("intent")): item
        for item in listing
        if isinstance(item, dict) and item.get("intent")
    }


def build_sector_action_governance_readiness() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    contract_registry = build_sector_action_contract_registry()
    registry_by_action = {row["sector_action"]: row for row in contract_registry["contracts"]}
    for pack_id, module_name, class_name in PORTFOLIO:
        plugin = _plugin(module_name, class_name)
        manifest = plugin.get_manifest()
        command_meta = _command_metadata(plugin)
        policies = list(manifest.policies)
        for action in manifest.external_actions:
            intent = action.rsplit(".", 1)[-1]
            command = command_meta.get(intent, {})
            governed = command.get("governed_action_type")
            matching_policies = [
                p for p in policies
                if any(token in p for token in (intent.split("_")[0], "authorization", "integrity"))
            ]
            # The heuristic above is only discovery context; it is never used
            # as an authorization decision or risk assignment.
            registry = registry_by_action[action]
            kernel_contract = registry["action_kernel"]["contract"]
            if registry.get("execution_kind") == "INTERNAL_DOMAIN":
                has_execution_evidence = (
                    registry["action_kernel"]["binding"] == "BOUND"
                    and registry["connector"].get("binding") == "NOT_REQUIRED_INTERNAL"
                    and registry["receipt"] != "NOT_EXPOSED"
                    and registry["verification"] != "NOT_EXPOSED"
                    and registry["reconciliation"] == "NOT_REQUIRED_INTERNAL"
                )
            else:
                has_execution_evidence = (
                    registry["action_kernel"]["binding"] == "BOUND"
                    and registry["connector"].get("binding") == "BOUND"
                    and registry["receipt"] != "NOT_EXPOSED"
                    and registry["verification"] != "NOT_EXPOSED"
                    and registry["reconciliation"] != "NOT_EXPOSED"
                )
            rows.append({
                "pack_id": pack_id,
                "sector_action": action,
                "governed_action_type": governed or (action if action == "publish_content" else "NOT_DECLARED"),
                "declared_parameters": list(command.get("params") or []),
                "declared_execution_mode": command.get("execution_mode", "NOT_DECLARED"),
                "declared_governance_policies": matching_policies,
                "governance_contract": {
                    "risk_level": kernel_contract.get("risk_level") if kernel_contract else "UNDECLARED",
                    "requires_approval": kernel_contract.get("requires_approval") if kernel_contract else "UNDECLARED",
                    "irreversible": kernel_contract.get("irreversible") if kernel_contract else "UNDECLARED",
                    "authorization_policy": "ACTION_KERNEL_CONTRACT" if kernel_contract else "UNDECLARED",
                },
                "connector": registry["connector"],
                "receipt": registry["receipt"],
                "verification": registry["verification"],
                "reconciliation": registry["reconciliation"],
                "registration_status": "READY_FOR_CANONICAL_EXECUTION_PATH" if has_execution_evidence else "BLOCKED_PENDING_CANONICAL_GOVERNANCE_AND_EXECUTION_EVIDENCE",
            })
    return {
        "schema": "KDO.SectorActionGovernanceReadiness/v1",
        "portfolio_version": "1.1",
        "read_only": True,
        "count": len(rows),
        "actions": rows,
        "summary": {
            "declared_actions": len(rows),
            "ready_for_canonical_execution_path": sum(1 for x in rows if x["registration_status"] == "READY_FOR_CANONICAL_EXECUTION_PATH"),
            "blocked_pending_canonical_governance_and_execution_evidence": sum(1 for x in rows if x["registration_status"] != "READY_FOR_CANONICAL_EXECUTION_PATH"),
            "risk_assignments_invented": 0,
            "provider_bindings_invented": 0,
            "receipt_contracts_invented": 0,
            "verification_strategies_invented": 0,
            "reconciliation_strategies_invented": 0,
        },
        "authority": {
            "read_only": True,
            "sector_packs_grant_execution_authority": False,
            "governance_readiness_grants_execution_authority": False,
            "action_kernel_remains_execution_authority": True,
            "connector_fabric_remains_provider_authority": True,
        },
    }
