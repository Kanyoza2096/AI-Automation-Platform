"""Canonical Finance payment execution bridge for Stripe PaymentIntent creation.

Stripe PaymentIntent creation is a provider receipt, not proof that funds were
successfully captured. The bridge therefore records the provider reference and
keeps the sector payment uncertain until authoritative lifecycle reconciliation.
"""
from __future__ import annotations
import hashlib
from typing import Any
from services.action_kernel import ActionContract

FINANCE_PAYMENT_ACTION_CONTRACT_SPEC = {
    "action_type": "finance_initiate_payment",
    "risk_level": "critical",
    "required_parameters": ("amount", "currency", "reference"),
    "requires_approval": True,
    "irreversible": True,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}


def _idempotency_key(workspace_id: str, reference: str) -> str:
    return "kdo-pay-" + hashlib.sha256(f"{workspace_id}|{reference}".encode()).hexdigest()[:48]


def register_finance_payment_action(kernel: Any, repository: Any, stripe_adapter: Any) -> str:
    async def execute_payment(parameters: dict[str, Any], intent: Any) -> dict[str, Any]:
        workspace_id = intent.workspace_id
        amount = float(parameters["amount"])
        currency = str(parameters["currency"]).upper()
        reference = str(parameters["reference"]).strip()
        if amount <= 0:
            raise ValueError("payment amount must be positive")
        if len(currency) != 3:
            raise ValueError("payment currency must be a 3-letter code")
        if not reference:
            raise ValueError("payment reference is required")

        idem = _idempotency_key(workspace_id, reference)
        payment = repository.create_payment(workspace_id, {
            "amount": amount, "currency": currency, "reference": reference,
            "idempotency_key": idem,
        })
        receipt = stripe_adapter.create_payment_intent(
            amount=int(round(amount * 100)),
            currency=currency,
            idempotency_key=idem,
            customer=parameters.get("customer"),
        )
        if not isinstance(receipt, dict):
            receipt = {"status": "uncertain", "outcome": "uncertain", "error": "invalid Stripe receipt"}
        provider_reference = receipt.get("provider_object_id") or receipt.get("observed_object_id") or receipt.get("id")
        repository.update_payment(
            workspace_id, payment.id,
            provider_payment_intent_id=str(provider_reference) if provider_reference else None,
            status="uncertain",
            provider_status=str(receipt.get("status") or receipt.get("outcome") or "unknown"),
            reconciliation_required=True,
            evidence_ref=str(receipt.get("provider_request_id") or receipt.get("request_id") or ""),
        )
        return {
            "ok": True,
            "outcome": "uncertain",
            "provider": "stripe",
            "payment_id": payment.id,
            "provider_object_id": provider_reference,
            "provider_receipt": receipt,
            "reconciliation_required": True,
            "payment_intent_is_business_outcome": False,
        }

    kernel.register(ActionContract(**FINANCE_PAYMENT_ACTION_CONTRACT_SPEC, handler=execute_payment))
    return FINANCE_PAYMENT_ACTION_CONTRACT_SPEC["action_type"]
