"""Read-only implementation gates for Sector Platform consequential actions.

This layer turns the sector action registry into an explicit engineering gate.
It does not register actions, execute providers, or infer missing contracts.
An action can only become READY when governance/action-kernel, connector,
receipt, verification, and reconciliation evidence are all code-backed.
"""
from __future__ import annotations

from typing import Any

from services.sector_action_contract_registry import build_sector_action_contract_registry


def _gate_for(record: dict[str, Any]) -> dict[str, Any]:
    kernel = record["action_kernel"]
    connector = record["connector"]
    receipt = record["receipt"]
    verification = record["verification"]
    reconciliation = record["reconciliation"]

    blockers: list[str] = []
    if kernel.get("binding") != "BOUND":
        blockers.append("ACTION_KERNEL_CONTRACT_MISSING")
    if record["governance"].get("risk_level") == "UNDECLARED":
        blockers.append("CANONICAL_GOVERNANCE_MISSING")
    execution_kind = str(record.get("execution_kind") or "EXTERNAL_PROVIDER")
    if execution_kind == "INTERNAL_DOMAIN":
        if connector.get("binding") not in {"NOT_REQUIRED_INTERNAL"}:
            blockers.append("INTERNAL_ACTION_CONNECTOR_MODEL_INVALID")
        if receipt == "NOT_EXPOSED":
            blockers.append("INTERNAL_RECEIPT_CONTRACT_MISSING")
        if verification == "NOT_EXPOSED":
            blockers.append("INTERNAL_DURABLE_WRITE_VERIFICATION_MISSING")
        if reconciliation != "NOT_REQUIRED_INTERNAL":
            blockers.append("INTERNAL_RECONCILIATION_MODEL_INVALID")
    else:
        if connector.get("binding") != "BOUND":
            blockers.append("CONNECTOR_FABRIC_BINDING_MISSING")
        if receipt == "NOT_EXPOSED":
            blockers.append("PROVIDER_RECEIPT_MAPPING_MISSING")
        if verification == "NOT_EXPOSED":
            blockers.append("VERIFICATION_STRATEGY_MISSING")
        if reconciliation == "NOT_EXPOSED":
            blockers.append("RECONCILIATION_STRATEGY_MISSING")

    if not blockers:
        status = "READY_FOR_CANONICAL_EXECUTION_PATH"
        next_step = "NONE"
    else:
        status = "BLOCKED"
        next_step = blockers[0]

    return {
        "status": status,
        "blockers": blockers,
        "next_engineering_gate": next_step,
        "authority": {
            "sector_pack": "NONE",
            "action_kernel": "REQUIRED",
            "connector_fabric": "REQUIRED",
        },
    }



# Evidence-backed candidates are discovery aids only. They never bind a sector
# action to a provider. The candidate is listed only where the clean backend has
# an existing governed provider action with an obviously adjacent domain.
_CANDIDATES: dict[str, tuple[dict[str, Any], ...]] = {
    "crm.send_message": (
        {
            "provider_action": "twilio.send_sms",
            "provider": "twilio",
            "status": "CANDIDATE_BOUND",
            "blockers": [],
        },
    ),
    "it.remediate_service": (
        {"provider_action":"github.dispatch_workflow","provider":"github","status":"CANDIDATE_BOUND","blockers":[]},
    ),
    "it.request_deployment": (
        {
            "provider_action": "render.trigger_deploy",
            "provider": "render",
            "status": "CANDIDATE_BOUND",
            "blockers": [],
        },
    ),
    "education.send_parent_message": (
        {"provider_action":"twilio.send_sms","provider":"twilio","status":"CANDIDATE_BOUND","blockers":[]},
    ),
    "healthcare.send_patient_message": (
        {"provider_action":"twilio.send_sms","provider":"twilio","status":"CANDIDATE_BOUND","blockers":[]},
    ),
    "commerce.send_order_notification": (
        {"provider_action":"twilio.send_sms","provider":"twilio","status":"CANDIDATE_BOUND","blockers":[]},
    ),
    "commerce.capture_payment": (
        {"provider_action":"stripe.capture_payment_intent","provider":"stripe","status":"CANDIDATE_BOUND","blockers":[]},
    ),
    "commerce.fulfill_order": (
        {"provider_action":"shopify.create_fulfillment","provider":"shopify","status":"CANDIDATE_BOUND","blockers":[]},
    ),
    "manufacturing.request_maintenance": (
        {"provider_action":"jira.create_issue","provider":"jira","status":"CANDIDATE_BOUND","blockers":[]},
    ),
    "government.notify_applicant": (
        {"provider_action":"twilio.send_sms","provider":"twilio","status":"CANDIDATE_BOUND","blockers":[]},
    ),
    "government.dispatch_field_service": (
        {"provider_action":"jira.create_issue","provider":"jira","status":"CANDIDATE_BOUND","blockers":[]},
    ),
    "finance.initiate_payment": (
        {
            "provider_action": "stripe.create_payment_intent",
            "provider": "stripe",
            "status": "CANDIDATE_BOUND",
            "blockers": [],
        },
    ),
    "energy.dispatch_crew": (
        {"provider_action":"jira.create_issue","provider":"jira","status":"CANDIDATE_BOUND","blockers":[]},
    ),
    "finance.send_statement": (
        {"provider_action":"twilio.send_sms","provider":"twilio","status":"CANDIDATE_BOUND","blockers":[]},
    ),
}

def build_sector_action_implementation_gate() -> dict[str, Any]:
    registry = build_sector_action_contract_registry()
    actions = []
    for record in registry["contracts"]:
        gate = _gate_for(record)
        actions.append({
            "pack_id": record["pack_id"],
            "sector_action": record["sector_action"],
            "governed_action_type": record["governed_action_type"],
            "provider_candidates": list(_CANDIDATES.get(record["sector_action"], ())),
            "gate": gate,
        })

    ready = [x for x in actions if x["gate"]["status"] == "READY_FOR_CANONICAL_EXECUTION_PATH"]
    blocked = [x for x in actions if x["gate"]["status"] == "BLOCKED"]
    blocker_counts: dict[str, int] = {}
    for item in blocked:
        for blocker in item["gate"]["blockers"]:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1

    return {
        "schema": "KDO.SectorActionImplementationGate/v1",
        "portfolio_version": registry["portfolio_version"],
        "read_only": True,
        "authority": registry["authority"],
        "summary": {
            "declared_actions": len(actions),
            "ready": len(ready),
            "blocked": len(blocked),
            "blocker_counts": dict(sorted(blocker_counts.items())),
        },
        "actions": actions,
    }
