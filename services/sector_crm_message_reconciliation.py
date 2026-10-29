"""Canonical Twilio message-delivery reconciliation for CRM."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_TERMINAL_SUCCESS = {"delivered", "received"}
_TERMINAL_FAILURE = {"undelivered", "failed", "canceled", "cancelled"}


class CRMMessageReconciliationService:
    def __init__(self, repository: Any, twilio_adapter: Any):
        self.repository = repository
        self.twilio_adapter = twilio_adapter

    def reconcile(self, *, workspace_id: str, delivery_id: str) -> dict[str, Any]:
        workspace_id = str(workspace_id)
        delivery = self.repository.get_message_delivery(workspace_id, str(delivery_id))
        if delivery is None:
            raise ValueError("message delivery not found in workspace")
        reference = str(delivery.provider_message_sid or "").strip()
        attempts = self.repository.list_message_reconciliation_evidence(workspace_id, delivery.id, limit=500)
        attempt = len(attempts) + 1
        if not reference:
            self.repository.record_message_reconciliation_evidence(workspace_id, {
                "delivery_id": delivery.id,
                "provider": "twilio",
                "provider_reference": "",
                "attempt_number": attempt,
                "provider_status": "unknown",
                "canonical_status": "operator_review",
                "terminal": False,
                "verification_state": "operator_review",
                "observed": {},
                "evidence": {"reason": "provider reference unavailable"},
            })
            return {"delivery_id": delivery.id, "status": "operator_review", "terminal": False, "reconciliation_required": True}

        observed = self.twilio_adapter.get_message(reference)
        if not isinstance(observed, dict):
            raise ValueError("invalid Twilio message response")
        provider_status = str(observed.get("status") or "unknown").lower()
        if provider_status in _TERMINAL_SUCCESS:
            canonical = "succeeded"
            terminal = True
        elif provider_status in _TERMINAL_FAILURE:
            canonical = "failed"
            terminal = True
        else:
            canonical = "in_progress"
            terminal = False

        self.repository.record_message_reconciliation_evidence(workspace_id, {
            "delivery_id": delivery.id,
            "provider": "twilio",
            "provider_reference": reference,
            "attempt_number": attempt,
            "provider_status": provider_status,
            "canonical_status": canonical,
            "terminal": terminal,
            "verification_state": "verified_provider_terminal" if terminal else "provider_observed_nonterminal",
            "observed": observed,
            "evidence": {"reconciled_at": datetime.now(timezone.utc).isoformat()},
        })
        updated = self.repository.update_message_delivery(
            workspace_id,
            delivery.id,
            provider_status=provider_status,
            status=canonical if terminal else "uncertain",
            reconciliation_required=not terminal,
            evidence={**delivery.evidence, "latest_reconciliation": observed},
        )
        return {
            "workspace_id": workspace_id,
            "delivery_id": delivery.id,
            "provider": "twilio",
            "provider_reference": reference,
            "provider_status": provider_status,
            "status": canonical,
            "terminal": terminal,
            "reconciliation_required": not terminal,
            "message_delivery_is_customer_outcome": False,
            "delivery": updated.model_dump(mode="json"),
        }
