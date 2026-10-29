"""Canonical internal Commerce record actions.

These actions persist domain state inside KDO; they are not external-provider
mutations. They therefore use the Action Kernel and durable repository as the
execution/receipt boundary and deliberately have no Connector Fabric binding.
"""
from __future__ import annotations
from typing import Any
from services.action_kernel import ActionContract, ActionIntent

COMMERCE_RECORD_PAYMENT_ACTION_CONTRACT_SPEC = {
    "action_type": "commerce_record_payment",
    "risk_level": "medium",
    "required_parameters": ("order_id", "amount", "currency", "status"),
    "requires_approval": False,
    "irreversible": False,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}
COMMERCE_RECORD_FULFILLMENT_ACTION_CONTRACT_SPEC = {
    "action_type": "commerce_record_fulfillment",
    "risk_level": "medium",
    "required_parameters": ("order_id", "status"),
    "requires_approval": False,
    "irreversible": False,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}
COMMERCE_RECORD_OUTCOME_ACTION_CONTRACT_SPEC = {
    "action_type": "commerce_record_outcome",
    "risk_level": "low",
    "required_parameters": ("metric", "value", "source", "evidence"),
    "requires_approval": False,
    "irreversible": False,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}


def _handler(method: str, repo):
    async def run(parameters: dict[str, Any], intent: ActionIntent) -> dict[str, Any]:
        payload = dict(parameters)
        value = getattr(repo, method)(intent.workspace_id, payload)
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


def register_commerce_internal_record_actions(kernel, commerce_repository) -> None:
    specs = (
        (COMMERCE_RECORD_PAYMENT_ACTION_CONTRACT_SPEC, "record_payment"),
        (COMMERCE_RECORD_FULFILLMENT_ACTION_CONTRACT_SPEC, "record_fulfillment"),
        (COMMERCE_RECORD_OUTCOME_ACTION_CONTRACT_SPEC, "record_outcome"),
    )
    for spec, method in specs:
        kernel.register(ActionContract(**spec, handler=_handler(method, commerce_repository)))
