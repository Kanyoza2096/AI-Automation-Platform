"""Canonical Commerce payment-capture bridge for Stripe PaymentIntents."""
from __future__ import annotations
from typing import Any
from services.action_kernel import ActionContract

COMMERCE_CAPTURE_PAYMENT_ACTION_CONTRACT_SPEC = {
    "action_type": "commerce_capture_payment",
    "risk_level": "critical",
    "required_parameters": ("order_id", "amount", "currency"),
    "requires_approval": True,
    "irreversible": True,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}


def register_commerce_capture_payment_action(kernel: Any, repository: Any, stripe_adapter: Any) -> str:
    async def execute_capture(parameters: dict[str, Any], intent: Any) -> dict[str, Any]:
        workspace_id = str(intent.workspace_id)
        order_id = str(parameters["order_id"])
        amount = float(parameters["amount"])
        currency = str(parameters["currency"]).upper()
        if amount <= 0 or len(currency) != 3:
            raise ValueError("valid positive amount and 3-letter currency are required")
        order = repository.get_order(workspace_id, order_id)
        if order is None:
            raise ValueError("order not found in workspace")
        if amount > order.total_amount:
            raise ValueError("payment amount exceeds order total")
        payment = repository.get_payment_for_order(workspace_id, order_id)
        if payment is None or not payment.provider_reference:
            raise ValueError("an existing provider payment reference is required before capture")
        if payment.status in {"captured", "succeeded"}:
            return {"ok": True, "outcome": "succeeded", "payment_id": payment.id, "provider_object_id": payment.provider_reference, "already_captured": True}
        receipt = stripe_adapter.capture_payment_intent(payment.provider_reference)
        if not isinstance(receipt, dict):
            receipt = {"status": "uncertain", "outcome": "uncertain", "error": "invalid Stripe capture receipt"}
        repository.update_payment(workspace_id, payment.id, status="uncertain", provider_status=str(receipt.get("status") or "unknown"), reconciliation_required=True)
        return {"ok": True, "outcome": "uncertain", "provider": "stripe", "payment_id": payment.id, "provider_object_id": payment.provider_reference, "provider_receipt": receipt, "reconciliation_required": True, "capture_is_business_outcome": False}

    kernel.register(ActionContract(**COMMERCE_CAPTURE_PAYMENT_ACTION_CONTRACT_SPEC, handler=execute_capture))
    return COMMERCE_CAPTURE_PAYMENT_ACTION_CONTRACT_SPEC["action_type"]
