from __future__ import annotations
import hashlib
from typing import Any
from services.action_kernel import ActionContract

GOVERNMENT_APPLICANT_NOTIFICATION_ACTION_CONTRACT_SPEC = {
    "action_type": "government_notify_applicant",
    "risk_level": "high",
    "required_parameters": ("permit_case_id", "applicant_ref", "to_number", "message", "from_", "reference"),
    "requires_approval": True,
    "irreversible": True,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}

def _key(workspace_id, permit_case_id, applicant_ref, message, reference):
    raw=f"{workspace_id}|{permit_case_id}|{applicant_ref}|{message}|{reference}".encode()
    return "kdo-gov-applicant-msg-" + hashlib.sha256(raw).hexdigest()[:48]

def register_government_applicant_notification_action(kernel: Any, government_repository: Any, notification_repository: Any, twilio_adapter: Any):
    async def execute(parameters: dict[str, Any], intent: Any):
        ws=str(intent.workspace_id); permit_id=str(parameters["permit_case_id"]).strip(); applicant_ref=str(parameters["applicant_ref"]).strip()
        to_number=str(parameters["to_number"]).strip(); message=str(parameters["message"]).strip(); from_=str(parameters["from_"]).strip(); reference=str(parameters["reference"]).strip()
        if not all((permit_id, applicant_ref, to_number, message, from_, reference)): raise ValueError("permit_case_id, applicant_ref, to_number, message, from_ and reference are required")
        permit=government_repository.get_permit(ws, permit_id)
        if permit is None: raise ValueError("permit case not found in workspace")
        if str(permit.applicant_ref) != applicant_ref: raise ValueError("applicant reference does not match permit case")
        key=_key(ws,permit_id,applicant_ref,message,reference)
        existing=notification_repository.get_by_idempotency_key(ws,key)
        if existing is not None:
            return {"ok":True,"outcome":"uncertain" if existing.status in {"pending","uncertain"} else existing.status,"provider":"twilio","notification_id":existing.id,"provider_object_id":existing.provider_message_sid,"provider_receipt_is_business_outcome":False,"reconciliation_required":existing.reconciliation_required}
        notification=notification_repository.create(ws,{"permit_case_id":permit_id,"applicant_ref":applicant_ref,"to_number":to_number,"body":message,"idempotency_key":key,"evidence":{"reference":reference,"intent_id":str(getattr(intent,"id",""))}})
        receipt=twilio_adapter.send_sms(from_=from_,to=to_number,body=message)
        if not isinstance(receipt,dict): receipt={"status":"uncertain","outcome":"uncertain","error":"invalid Twilio receipt"}
        provider_ref=receipt.get("provider_object_id") or receipt.get("observed_object_id") or receipt.get("id")
        provider_status=str(receipt.get("status") or receipt.get("outcome") or "unknown").lower()
        status="failed" if provider_status == "failed" else "uncertain"
        notification=notification_repository.update(ws,notification.id,provider_message_sid=str(provider_ref) if provider_ref else None,provider_status=provider_status,status=status,reconciliation_required=(status!="failed"),evidence={**notification.evidence,"provider_receipt":receipt,"from":from_})
        return {"ok":True,"outcome":status,"provider":"twilio","notification_id":notification.id,"provider_object_id":provider_ref,"provider_receipt_is_business_outcome":False,"reconciliation_required":notification.reconciliation_required}
    kernel.register(ActionContract(**GOVERNMENT_APPLICANT_NOTIFICATION_ACTION_CONTRACT_SPEC, handler=execute))
    return GOVERNMENT_APPLICANT_NOTIFICATION_ACTION_CONTRACT_SPEC["action_type"]
