"""Read-only semantic binding audit for unresolved Sector Platform actions.

This deliberately does not create provider bindings. It records why an action
cannot safely advance to the canonical execution gate with the currently
code-backed Connector Fabric.
"""
from __future__ import annotations
from typing import Any
from services.sector_action_implementation_gate import build_sector_action_implementation_gate

# Evidence-backed classifications. These are engineering review states, not
# execution authority and not provider recommendations.
_CLASSIFICATIONS = {
    "marketing_schedule_content": ("INTERNAL_DOMAIN_STATE", "Scheduling is represented by the marketing repository/domain model; no code-backed external provider mutation is exposed."),
    "commerce.record_payment": ("INTERNAL_OUTCOME_RECORD", "Records sector payment state; no external provider receipt is defined for the record action."),
    "commerce.record_fulfillment": ("INTERNAL_OUTCOME_RECORD", "Records fulfillment state; external fulfillment is already represented by commerce.fulfill_order."),
    "commerce.record_outcome": ("INTERNAL_OUTCOME_RECORD", "Records canonical commerce outcome; provider mutation would conflate outcome recording with external execution."),
    "security.contain_asset": ("NO_SEMANTIC_PROVIDER_BINDING", "Existing providers do not expose a code-backed asset-containment contract matching this cybersecurity action."),
    "security.remediate_finding": ("NO_SEMANTIC_PROVIDER_BINDING", "Existing providers do not expose a code-backed finding-remediation contract matching this cybersecurity action."),
    "security.record_response_outcome": ("INTERNAL_OUTCOME_RECORD", "Records security response evidence rather than performing provider mutation."),
    "security.record_outcome": ("INTERNAL_OUTCOME_RECORD", "Records canonical security outcome rather than performing provider mutation."),
    "crm.record_activity": ("INTERNAL_OUTCOME_RECORD", "Records CRM activity; crm.send_message is the external communication action."),
    "crm.record_outcome": ("INTERNAL_OUTCOME_RECORD", "Records canonical CRM outcome rather than performing provider mutation."),
    "education.record_intervention": ("INTERNAL_OUTCOME_RECORD", "Records intervention evidence; parent messaging is the external communication action."),
    "education.record_outcome": ("INTERNAL_OUTCOME_RECORD", "Records canonical education outcome rather than performing provider mutation."),
    "healthcare.record_care_plan": ("INTERNAL_OUTCOME_RECORD", "Records care-plan state; patient messaging is the external communication action."),
    "healthcare.record_outcome": ("INTERNAL_OUTCOME_RECORD", "Records canonical healthcare outcome rather than performing provider mutation."),
    "supply_chain.dispatch_shipment": ("NO_SEMANTIC_PROVIDER_BINDING", "The sector exposes connector-deferred logistics operations but no concrete carrier/provider action contract is currently code-backed."),
    "supply_chain.request_carrier_booking": ("NO_SEMANTIC_PROVIDER_BINDING", "Carrier booking requires a concrete carrier connector contract and authoritative receipt/reconciliation strategy."),
    "supply_chain.record_exception": ("INTERNAL_OUTCOME_RECORD", "Records logistics exception evidence rather than performing provider mutation."),
    "supply_chain.record_outcome": ("INTERNAL_OUTCOME_RECORD", "Records canonical logistics outcome rather than performing provider mutation."),
    "manufacturing.release_production": ("NO_SEMANTIC_PROVIDER_BINDING", "Production release is a manufacturing-domain operation; the existing AWS ECS contract is not a manufacturing release contract."),
    "energy.issue_remote_command": ("NO_SEMANTIC_PROVIDER_BINDING", "No existing provider contract represents a utility-asset remote command with authoritative verification semantics."),
    "energy.record_exception": ("INTERNAL_OUTCOME_RECORD", "Records utility exception evidence rather than performing provider mutation."),
    "energy.record_outcome": ("INTERNAL_OUTCOME_RECORD", "Records canonical utility outcome rather than performing provider mutation."),
    "government.record_resolution": ("INTERNAL_OUTCOME_RECORD", "Records field-service/case resolution rather than performing provider mutation."),
    "government.record_outcome": ("INTERNAL_OUTCOME_RECORD", "Records canonical government outcome rather than performing provider mutation."),
    "agriculture.dispatch_field_task": ("NO_SEMANTIC_PROVIDER_BINDING", "No existing provider action represents agricultural field-task dispatch with authoritative receipt/reconciliation."),
    "agriculture.request_input_delivery": ("NO_SEMANTIC_PROVIDER_BINDING", "No existing provider action represents agricultural input delivery with domain-specific receipt/reconciliation."),
    "agriculture.record_exception": ("INTERNAL_OUTCOME_RECORD", "Records agricultural exception evidence rather than performing provider mutation."),
    "agriculture.record_outcome": ("INTERNAL_OUTCOME_RECORD", "Records canonical agricultural outcome rather than performing provider mutation."),
}

def build_sector_action_semantic_binding_audit() -> dict[str, Any]:
    gate = build_sector_action_implementation_gate()
    rows=[]
    for item in gate["actions"]:
        if item["gate"]["status"] != "BLOCKED":
            continue
        action=item["sector_action"]
        classification, reason = _CLASSIFICATIONS.get(action, ("UNCLASSIFIED", "No semantic classification is code-backed yet."))
        rows.append({"sector_action":action,"classification":classification,"reason":reason,"next_gate":item["gate"]["next_engineering_gate"]})
    counts={}
    for row in rows: counts[row["classification"]]=counts.get(row["classification"],0)+1
    return {"schema":"KDO.SectorActionSemanticBindingAudit/v1","read_only":True,"summary":{"blocked_actions":len(rows),"classifications":dict(sorted(counts.items()))},"actions":rows}
