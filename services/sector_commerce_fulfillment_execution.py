"""Canonical Commerce fulfillment bridge for Shopify Fulfillment orders."""
from __future__ import annotations
from typing import Any
from services.action_kernel import ActionContract

COMMERCE_FULFILL_ORDER_ACTION_CONTRACT_SPEC = {
    "action_type": "commerce_fulfill_order",
    "risk_level": "critical",
    "required_parameters": ("order_id",),
    "requires_approval": True,
    "irreversible": True,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}

def register_commerce_fulfill_order_action(kernel: Any, repository: Any, shopify_adapter: Any) -> str:
    async def execute(parameters: dict[str, Any], intent: Any) -> dict[str, Any]:
        workspace_id = str(intent.workspace_id); order_id = str(parameters["order_id"])
        order = repository.get_order(workspace_id, order_id)
        if order is None: raise ValueError("order not found in workspace")
        if order.fulfillment_status in {"shipped", "delivered"}:
            return {"ok": True, "outcome": "succeeded", "already_fulfilled": True}
        receipt = shopify_adapter.create_fulfillment(order_id, notify_customer=bool(parameters.get("notify_customer", False)), message=parameters.get("message"))
        if not isinstance(receipt, dict): receipt = {"status":"uncertain","outcome":"uncertain","error":"invalid Shopify fulfillment receipt"}
        return {"ok": True, "outcome": "uncertain", "provider":"shopify", "provider_object_id": receipt.get("provider_object_id"), "provider_receipt": receipt, "reconciliation_required": True, "fulfillment_is_business_outcome": False}
    kernel.register(ActionContract(**COMMERCE_FULFILL_ORDER_ACTION_CONTRACT_SPEC, handler=execute))
    return COMMERCE_FULFILL_ORDER_ACTION_CONTRACT_SPEC["action_type"]
