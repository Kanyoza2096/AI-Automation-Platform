"""Canonical post-capture reconciliation for Commerce payments."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

_TERMINAL={"succeeded","canceled"}
class CommercePaymentReconciliationService:
    def __init__(self, repository: Any, stripe_adapter: Any): self.repository,self.stripe_adapter=repository,stripe_adapter
    def reconcile(self, *, workspace_id: str, payment_id: str, provider_reference: str|None=None) -> dict[str,Any]:
        payment=self.repository.get_payment(str(workspace_id),str(payment_id))
        if payment is None: raise ValueError("payment not found in workspace")
        reference=str(provider_reference or payment.provider_reference or "").strip()
        if not reference: raise ValueError("provider payment reference is required")
        attempts=self.repository.list_payment_reconciliation_evidence(str(workspace_id),str(payment_id),limit=500)
        observed=self.stripe_adapter.get_payment_intent(reference)
        if not isinstance(observed,dict): raise ValueError("invalid Stripe payment intent response")
        provider_status=str(observed.get("status") or "unknown").lower()
        canonical="succeeded" if provider_status=="succeeded" else "failed" if provider_status=="canceled" else "in_progress"
        terminal=provider_status in _TERMINAL
        self.repository.record_payment_reconciliation_evidence(str(workspace_id),{"payment_id":str(payment_id),"provider":"stripe","provider_reference":reference,"attempt_number":len(attempts)+1,"provider_status":provider_status,"canonical_status":canonical,"terminal":terminal,"verification_state":"verified_provider_terminal" if terminal else "provider_observed_nonterminal","observed":observed,"evidence":{"reconciled_at":datetime.now(timezone.utc).isoformat()}})
        updated=self.repository.update_payment(str(workspace_id),str(payment_id),provider_reference=reference,status="captured" if canonical=="succeeded" else ("failed" if terminal else "uncertain"),reconciliation_required=not terminal,updated_at=datetime.now(timezone.utc))
        return {"workspace_id":str(workspace_id),"payment_id":str(payment_id),"provider":"stripe","provider_reference":reference,"provider_status":provider_status,"status":canonical,"terminal":terminal,"reconciliation_required":not terminal,"capture_is_business_outcome":False,"payment":updated.model_dump(mode="json")}
