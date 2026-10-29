"""Canonical Twilio patient-message reconciliation for Healthcare."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
_SUCCESS={"delivered","received"}; _FAILURE={"undelivered","failed","canceled","cancelled"}
class HealthcarePatientMessageReconciliationService:
    def __init__(self,repository:Any,twilio_adapter:Any): self.repository,self.twilio_adapter=repository,twilio_adapter
    def reconcile(self,*,workspace_id:str,delivery_id:str)->dict[str,Any]:
        delivery=self.repository.get_patient_message_delivery(str(workspace_id),str(delivery_id))
        if delivery is None: raise ValueError("patient message delivery not found in workspace")
        attempts=self.repository.list_patient_message_reconciliation_evidence(str(workspace_id),delivery.id,limit=500); attempt=len(attempts)+1
        reference=str(delivery.provider_message_sid or "").strip()
        if not reference:
            self.repository.record_patient_message_reconciliation_evidence(str(workspace_id),{"delivery_id":delivery.id,"provider":"twilio","provider_reference":"","attempt_number":attempt,"provider_status":"unknown","canonical_status":"operator_review","terminal":False,"verification_state":"operator_review","observed":{},"evidence":{"reason":"provider reference unavailable"}})
            return {"delivery_id":delivery.id,"status":"operator_review","terminal":False,"reconciliation_required":True}
        observed=self.twilio_adapter.get_message(reference)
        if not isinstance(observed,dict): raise ValueError("invalid Twilio message response")
        provider_status=str(observed.get("status") or "unknown").lower()
        if provider_status in _SUCCESS: canonical,terminal="succeeded",True
        elif provider_status in _FAILURE: canonical,terminal="failed",True
        else: canonical,terminal="in_progress",False
        self.repository.record_patient_message_reconciliation_evidence(str(workspace_id),{"delivery_id":delivery.id,"provider":"twilio","provider_reference":reference,"attempt_number":attempt,"provider_status":provider_status,"canonical_status":canonical,"terminal":terminal,"verification_state":"verified_provider_terminal" if terminal else "provider_observed_nonterminal","observed":observed,"evidence":{"reconciled_at":datetime.now(timezone.utc).isoformat()}})
        updated=self.repository.update_patient_message_delivery(str(workspace_id),delivery.id,provider_status=provider_status,status=canonical if terminal else "uncertain",reconciliation_required=not terminal,evidence={**delivery.evidence,"latest_reconciliation":observed})
        return {"workspace_id":str(workspace_id),"delivery_id":delivery.id,"provider":"twilio","provider_reference":reference,"provider_status":provider_status,"status":canonical,"terminal":terminal,"reconciliation_required":not terminal,"patient_message_delivery_is_care_outcome":False,"delivery":updated.model_dump(mode="json")}
