from __future__ import annotations
from datetime import datetime, timezone
_SUCCESS = {"delivered", "received"}
_FAILURE = {"undelivered", "failed", "canceled", "cancelled"}

class FinanceStatementReconciliationService:
    def __init__(self, repository, twilio_adapter):
        self.repository, self.twilio_adapter = repository, twilio_adapter

    def reconcile(self, *, workspace_id: str, statement_delivery_id: str):
        ws = str(workspace_id)
        delivery = self.repository.get_statement_delivery(ws, str(statement_delivery_id))
        if delivery is None:
            raise ValueError("statement delivery not found in workspace")
        attempts = self.repository.list_statement_reconciliation_evidence(ws, delivery.id)
        attempt = len(attempts) + 1
        ref = str(delivery.provider_message_sid or "").strip()
        if not ref:
            self.repository.record_statement_reconciliation_evidence(ws, {
                "statement_delivery_id": delivery.id, "provider": "twilio", "provider_reference": "",
                "attempt_number": attempt, "provider_status": "unknown", "canonical_status": "operator_review",
                "terminal": False, "verification_state": "operator_review", "observed": {},
                "evidence": {"reason": "provider reference unavailable"},
            })
            return {"statement_delivery_id": delivery.id, "status": "operator_review", "terminal": False, "reconciliation_required": True}
        observed = self.twilio_adapter.get_message(ref)
        if not isinstance(observed, dict):
            raise ValueError("invalid Twilio message response")
        provider_status = str(observed.get("status") or "unknown").lower()
        if provider_status in _SUCCESS:
            canonical, terminal = "succeeded", True
        elif provider_status in _FAILURE:
            canonical, terminal = "failed", True
        else:
            canonical, terminal = "in_progress", False
        self.repository.record_statement_reconciliation_evidence(ws, {
            "statement_delivery_id": delivery.id, "provider": "twilio", "provider_reference": ref,
            "attempt_number": attempt, "provider_status": provider_status, "canonical_status": canonical,
            "terminal": terminal, "verification_state": "verified_provider_terminal" if terminal else "provider_observed_nonterminal",
            "observed": observed, "evidence": {"reconciled_at": datetime.now(timezone.utc).isoformat()},
        })
        updated = self.repository.update_statement_delivery(ws, delivery.id, provider_status=provider_status,
            status=canonical if terminal else "uncertain", reconciliation_required=not terminal,
            evidence={**delivery.evidence, "latest_reconciliation": observed})
        return {"workspace_id": ws, "statement_delivery_id": delivery.id, "provider": "twilio",
                "provider_reference": ref, "provider_status": provider_status, "status": canonical,
                "terminal": terminal, "reconciliation_required": not terminal,
                "statement_delivery_is_business_outcome": False,
                "delivery": updated.model_dump(mode="json")}
