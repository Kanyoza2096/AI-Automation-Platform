from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
_SUCCESS={"delivered","received"}; _FAILURE={"undelivered","failed","canceled","cancelled"}
class GovernmentApplicantNotificationReconciliationService:
    def __init__(self, repository, twilio_adapter): self.repository,self.twilio_adapter=repository,twilio_adapter
    def reconcile(self, *, workspace_id, notification_id):
        n=self.repository.get(str(workspace_id),str(notification_id))
        if n is None: raise ValueError("applicant notification not found in workspace")
        attempts=self.repository.list_evidence(str(workspace_id),n.id); attempt=len(attempts)+1; ref=str(n.provider_message_sid or "").strip()
        if not ref:
            self.repository.record_evidence(str(workspace_id),{"notification_id":n.id,"provider":"twilio","provider_reference":"","attempt_number":attempt,"provider_status":"unknown","canonical_status":"operator_review","terminal":False,"verification_state":"operator_review","observed":{},"evidence":{"reason":"provider reference unavailable"}})
            return {"notification_id":n.id,"status":"operator_review","terminal":False,"reconciliation_required":True}
        observed=self.twilio_adapter.get_message(ref)
        status=str((observed or {}).get("status") or "unknown").lower()
        if status in _SUCCESS: canonical,terminal="succeeded",True
        elif status in _FAILURE: canonical,terminal="failed",True
        else: canonical,terminal="in_progress",False
        self.repository.record_evidence(str(workspace_id),{"notification_id":n.id,"provider":"twilio","provider_reference":ref,"attempt_number":attempt,"provider_status":status,"canonical_status":canonical,"terminal":terminal,"verification_state":"verified_provider_terminal" if terminal else "provider_observed_nonterminal","observed":observed,"evidence":{"reconciled_at":datetime.now(timezone.utc).isoformat()}})
        updated=self.repository.update(str(workspace_id),n.id,provider_status=status,status=canonical if terminal else "uncertain",reconciliation_required=not terminal,evidence={**n.evidence,"latest_reconciliation":observed})
        return {"workspace_id":str(workspace_id),"notification_id":n.id,"provider":"twilio","provider_reference":ref,"provider_status":status,"status":canonical,"terminal":terminal,"reconciliation_required":not terminal,"notification_is_business_outcome":False}
