"""Canonical Stripe PaymentIntent lifecycle reconciliation for Finance."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

_TERMINAL = {"succeeded", "canceled"}
_SUCCESS = {"succeeded"}
_FAILURE = {"canceled"}

class FinancePaymentReconciliationService:
    def __init__(self, repository: Any, stripe_adapter: Any):
        self.repository = repository
        self.stripe_adapter = stripe_adapter

    def reconcile(self, *, workspace_id: str, payment_id: str, provider_reference: str | None = None) -> dict[str, Any]:
        workspace_id = str(workspace_id)
        payment_id = str(payment_id)
        payment = self.repository.get_payment(workspace_id, payment_id)
        if payment is None:
            raise ValueError("payment not found in workspace")
        reference = str(provider_reference or payment.provider_payment_intent_id or "").strip()
        if not reference:
            raise ValueError("Stripe payment intent reference is required")

        attempts = self.repository.list_payment_reconciliation_evidence(workspace_id, payment_id, limit=500)
        attempt = len(attempts) + 1
        observed = self.stripe_adapter.get_payment_intent(reference)
        if not isinstance(observed, dict):
            raise ValueError("invalid Stripe payment intent response")
        provider_status = str(observed.get("status") or "unknown").lower()
        if provider_status in _SUCCESS:
            canonical_status = "succeeded"
        elif provider_status in _FAILURE:
            canonical_status = "failed"
        else:
            canonical_status = "in_progress"

        terminal = provider_status in _TERMINAL
        self.repository.record_payment_reconciliation_evidence(workspace_id, {
            "payment_id": payment_id,
            "provider": "stripe",
            "provider_reference": reference,
            "attempt_number": attempt,
            "provider_status": provider_status,
            "canonical_status": canonical_status,
            "terminal": terminal,
            "verification_state": "verified_provider_terminal" if terminal else "provider_observed_nonterminal",
            "observed": observed,
            "evidence": {"reconciled_at": datetime.now(timezone.utc).isoformat()},
        })
        updated = self.repository.update_payment(
            workspace_id, payment_id,
            provider_payment_intent_id=reference,
            status=canonical_status if terminal else "uncertain",
            provider_status=provider_status,
            reconciliation_required=not terminal,
            evidence_ref=f"stripe:{reference}",
        )
        if terminal:
            outcome = self.repository.record_outcome(workspace_id, {
                "reference_type": "payment",
                "reference_id": payment_id,
                "outcome_type": "payment_terminal",
                "status": canonical_status,
                "amount": payment.amount,
                "evidence_ref": f"stripe:{reference}",
            })
        else:
            outcome = None
        return {
            "workspace_id": workspace_id,
            "payment_id": payment_id,
            "provider": "stripe",
            "provider_reference": reference,
            "provider_status": provider_status,
            "status": canonical_status,
            "terminal": terminal,
            "reconciliation_required": not terminal,
            "payment_intent_is_business_outcome": False,
            "outcome_id": getattr(outcome, "id", None),
            "observed": observed,
            "payment": updated.model_dump(mode="json"),
        }
