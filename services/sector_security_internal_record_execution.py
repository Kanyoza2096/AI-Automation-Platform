"""Canonical internal cybersecurity outcome-record actions.

These actions persist observed security state inside KDO. They do not mutate an
external provider and therefore terminate at the Action Kernel + durable
security repository boundary.
"""
from __future__ import annotations
from typing import Any
from services.action_kernel import ActionContract, ActionIntent

SECURITY_RECORD_RESPONSE_OUTCOME_ACTION_CONTRACT_SPEC = {
    "action_type": "security_record_response_outcome",
    "risk_level": "medium",
    "required_parameters": ("action_type", "target_id", "status", "evidence"),
    "requires_approval": False,
    "irreversible": False,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}

SECURITY_RECORD_OUTCOME_ACTION_CONTRACT_SPEC = {
    "action_type": "security_record_outcome",
    "risk_level": "low",
    "required_parameters": ("resource_type", "metric", "value", "source", "evidence"),
    "requires_approval": False,
    "irreversible": False,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}


def _handler(method: str, repo):
    async def run(parameters: dict[str, Any], intent: ActionIntent) -> dict[str, Any]:
        value = getattr(repo, method)(intent.workspace_id, dict(parameters))
        return {
            "ok": True,
            "status": "succeeded",
            "internal_receipt": {
                "kind": "KDOInternalDomainReceipt",
                "action_type": intent.action_type,
                "domain_operation": method,
                "object_id": getattr(value, "id", None),
            },
            "object_id": getattr(value, "id", None),
            "data": value.model_dump(mode="json"),
        }
    return run


def register_security_internal_record_actions(kernel, security_repository) -> None:
    for spec, method in (
        (SECURITY_RECORD_RESPONSE_OUTCOME_ACTION_CONTRACT_SPEC, "record_response_outcome"),
        (SECURITY_RECORD_OUTCOME_ACTION_CONTRACT_SPEC, "record_outcome"),
    ):
        kernel.register(ActionContract(**spec, handler=_handler(method, security_repository)))
