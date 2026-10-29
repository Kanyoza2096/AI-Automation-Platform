"""Canonical internal domain-record actions across Sector Packs.

These actions persist KDO-owned sector state through the Action Kernel. They do
not infer or invent external provider bindings.
"""
from __future__ import annotations
from typing import Any
from services.action_kernel import ActionContract, ActionIntent

SPECS = {
    "crm_record_activity": {"risk_level":"low","required_parameters":("activity_type",)},
    "crm_record_outcome": {"risk_level":"low","required_parameters":("metric","value","source","evidence")},
    "education_record_intervention": {"risk_level":"medium","required_parameters":("student_id","intervention_type")},
    "education_record_outcome": {"risk_level":"low","required_parameters":("metric","value","source","evidence")},
    "healthcare_record_care_plan": {"risk_level":"medium","required_parameters":("patient_id","plan_type")},
    "healthcare_record_outcome": {"risk_level":"low","required_parameters":("metric","value","source","evidence")},
    "supply_chain_record_exception": {"risk_level":"medium","required_parameters":("shipment_id","exception_type","severity")},
    "supply_chain_record_outcome": {"risk_level":"low","required_parameters":("reference_type","reference_id","outcome_type")},
    "energy_record_exception": {"risk_level":"medium","required_parameters":("asset_id","exception_type","severity")},
    "energy_record_outcome": {"risk_level":"low","required_parameters":("reference_type","reference_id","outcome_type")},
    "government_record_resolution": {"risk_level":"medium","required_parameters":("request_id","resolution")},
    "government_record_outcome": {"risk_level":"low","required_parameters":("reference_type","reference_id","outcome_type")},
    "agriculture_record_exception": {"risk_level":"medium","required_parameters":("field_id","exception_type","severity")},
    "agriculture_record_outcome": {"risk_level":"low","required_parameters":("reference_type","reference_id","outcome_type")},
}

ACTION_CONTRACT_SPECS = {
    action_type: {"action_type": action_type, **spec, "requires_approval": False, "irreversible": False, "inline_with_decision": True, "autonomous_retryable": False}
    for action_type, spec in SPECS.items()
}

_BINDINGS = {
    "crm_record_activity": ("crm_repository", "add_activity"),
    "crm_record_outcome": ("crm_repository", "record_outcome"),
    "education_record_intervention": ("education_repository", "record_intervention"),
    "education_record_outcome": ("education_repository", "record_outcome"),
    "healthcare_record_care_plan": ("healthcare_repository", "record_care_plan"),
    "healthcare_record_outcome": ("healthcare_repository", "record_outcome"),
    "supply_chain_record_exception": ("supply_chain_repository", "record_exception"),
    "supply_chain_record_outcome": ("supply_chain_repository", "record_outcome"),
    "energy_record_exception": ("energy_repository", "record_exception"),
    "energy_record_outcome": ("energy_repository", "record_outcome"),
    "government_record_resolution": ("government_repository", "record_resolution"),
    "government_record_outcome": ("government_repository", "record_outcome"),
    "agriculture_record_exception": ("agriculture_repository", "record_exception"),
    "agriculture_record_outcome": ("agriculture_repository", "record_outcome"),
}

def _handler(method: str, repository):
    async def run(parameters: dict[str, Any], intent: ActionIntent) -> dict[str, Any]:
        value = getattr(repository, method)(intent.workspace_id, dict(parameters))
        return {
            "ok": True, "status": "succeeded",
            "internal_receipt": {"kind":"KDOInternalDomainReceipt", "action_type":intent.action_type, "domain_operation":method, "object_id":getattr(value,"id",None)},
            "object_id": getattr(value, "id", None), "data": value.model_dump(mode="json"),
        }
    return run

def register_sector_internal_record_actions(kernel, **repositories) -> None:
    for action_type, (repo_key, method) in _BINDINGS.items():
        repository = repositories.get(repo_key)
        if repository is not None:
            kernel.register(ActionContract(**ACTION_CONTRACT_SPECS[action_type], handler=_handler(method, repository)))
