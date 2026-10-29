"""Canonical education parent-message execution bridge for Twilio."""
from __future__ import annotations
import hashlib
from typing import Any
from services.action_kernel import ActionContract

EDUCATION_PARENT_MESSAGE_ACTION_CONTRACT_SPEC = {
    "action_type": "education_send_parent_message",
    "risk_level": "high",
    "required_parameters": ("student_id", "message", "from_", "reference"),
    "requires_approval": True,
    "irreversible": True,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}

def _delivery_key(workspace_id: str, reference: str, student_id: str, body: str) -> str:
    raw = f"{workspace_id}|{reference}|{student_id}|{body}".encode()
    return "kdo-edu-parent-msg-" + hashlib.sha256(raw).hexdigest()[:48]

def register_education_parent_message_action(kernel: Any, repository: Any, twilio_adapter: Any) -> str:
    async def execute_message(parameters: dict[str, Any], intent: Any) -> dict[str, Any]:
        workspace_id = str(intent.workspace_id)
        student_id = str(parameters["student_id"]).strip()
        message = str(parameters["message"]).strip()
        from_ = str(parameters["from_"]).strip()
        reference = str(parameters["reference"]).strip()
        if not student_id or not message or not from_ or not reference:
            raise ValueError("student_id, message, from_ and reference are required")
        student = repository.get_student(workspace_id, student_id)
        if student is None:
            raise ValueError("student not found in workspace")
        to_number = str(student.guardian_phone or "").strip()
        if not to_number:
            raise ValueError("student guardian phone is required for parent messaging")
        key = _delivery_key(workspace_id, reference, student_id, message)
        existing = repository.get_parent_message_delivery_by_idempotency_key(workspace_id, key)
        if existing is not None:
            return {"ok": True, "outcome": "uncertain" if existing.status == "uncertain" else existing.status,
                    "provider": "twilio", "delivery_id": existing.id, "provider_object_id": existing.provider_message_sid,
                    "provider_receipt_is_delivery_outcome": False, "reconciliation_required": existing.reconciliation_required,
                    "delivery": existing.model_dump(mode="json")}
        delivery = repository.create_parent_message_delivery(workspace_id, {
            "student_id": student_id, "to_number": to_number, "body": message,
            "idempotency_key": key, "provider": "twilio", "status": "pending",
            "reconciliation_required": True,
            "evidence": {"reference": reference, "intent_id": str(getattr(intent, "id", ""))},
        })
        receipt = twilio_adapter.send_sms(from_=from_, to=to_number, body=message)
        if not isinstance(receipt, dict):
            receipt = {"status": "uncertain", "outcome": "uncertain", "error": "invalid Twilio receipt"}
        provider_reference = receipt.get("provider_object_id") or receipt.get("observed_object_id") or receipt.get("id")
        provider_status = str(receipt.get("status") or receipt.get("outcome") or "unknown").lower()
        canonical = "failed" if provider_status == "failed" else "uncertain"
        delivery = repository.update_parent_message_delivery(
            workspace_id, delivery.id,
            provider_message_sid=str(provider_reference) if provider_reference else None,
            provider_status=provider_status, status=canonical,
            reconciliation_required=(canonical != "failed"),
            evidence={**delivery.evidence, "provider_receipt": receipt, "from": from_},
        )
        return {"ok": True, "outcome": canonical, "provider": "twilio", "delivery_id": delivery.id,
                "provider_object_id": provider_reference, "provider_receipt_is_delivery_outcome": False,
                "reconciliation_required": delivery.reconciliation_required, "delivery": delivery.model_dump(mode="json")}
    kernel.register(ActionContract(**EDUCATION_PARENT_MESSAGE_ACTION_CONTRACT_SPEC, handler=execute_message))
    return EDUCATION_PARENT_MESSAGE_ACTION_CONTRACT_SPEC["action_type"]
