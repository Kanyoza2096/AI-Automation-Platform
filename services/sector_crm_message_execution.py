"""Canonical CRM outbound-message execution bridge for Twilio.

The Twilio send receipt proves provider acceptance/creation only. It is never
promoted directly to a CRM communication business outcome; delivery is
verified by authoritative provider reconciliation.
"""
from __future__ import annotations

import hashlib
from typing import Any

from services.action_kernel import ActionContract

CRM_MESSAGE_ACTION_CONTRACT_SPEC = {
    "action_type": "crm_send_message",
    "risk_level": "high",
    "required_parameters": ("message", "status", "from_", "reference"),
    "requires_approval": True,
    "irreversible": True,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}


def _delivery_key(workspace_id: str, reference: str, contact_id: str, body: str) -> str:
    raw = f"{workspace_id}|{reference}|{contact_id}|{body}".encode()
    return "kdo-crm-msg-" + hashlib.sha256(raw).hexdigest()[:48]


def register_crm_message_action(kernel: Any, repository: Any, twilio_adapter: Any) -> str:
    async def execute_message(parameters: dict[str, Any], intent: Any) -> dict[str, Any]:
        workspace_id = str(intent.workspace_id)
        message = str(parameters["message"]).strip()
        status = str(parameters.get("status") or "lead").strip().lower()
        from_ = str(parameters["from_"]).strip()
        reference = str(parameters["reference"]).strip()
        if not message:
            raise ValueError("message is required")
        if not from_:
            raise ValueError("from_ is required")
        if not reference:
            raise ValueError("reference is required")

        contacts = [c for c in repository.find_contacts_by_status(workspace_id, status) if str(c.phone).strip()]
        if not contacts:
            raise ValueError("no CRM contacts with phone numbers matched the requested status")

        deliveries = []
        for contact in contacts:
            key = _delivery_key(workspace_id, reference, str(contact.id), message)
            existing = repository.get_message_delivery_by_idempotency_key(workspace_id, key)
            if existing is not None:
                deliveries.append(existing.model_dump(mode="json"))
                continue

            delivery = repository.create_message_delivery(workspace_id, {
                "contact_id": str(contact.id),
                "to_number": str(contact.phone).strip(),
                "body": message,
                "idempotency_key": key,
                "provider": "twilio",
                "status": "pending",
                "reconciliation_required": True,
                "evidence": {"reference": reference, "intent_id": str(getattr(intent, "id", ""))},
            })
            receipt = twilio_adapter.send_sms(from_=from_, to=delivery.to_number, body=message)
            if not isinstance(receipt, dict):
                receipt = {"status": "uncertain", "outcome": "uncertain", "error": "invalid Twilio receipt"}
            provider_reference = receipt.get("provider_object_id") or receipt.get("observed_object_id") or receipt.get("id")
            provider_status = str(receipt.get("status") or receipt.get("outcome") or "unknown").lower()
            if provider_status == "failed":
                canonical = "failed"
                needs_reconciliation = False
            else:
                canonical = "uncertain"
                needs_reconciliation = True
            delivery = repository.update_message_delivery(
                workspace_id,
                delivery.id,
                provider_message_sid=str(provider_reference) if provider_reference else None,
                provider_status=provider_status,
                status=canonical,
                reconciliation_required=needs_reconciliation,
                evidence={**delivery.evidence, "provider_receipt": receipt, "from": from_},
            )
            deliveries.append(delivery.model_dump(mode="json"))

        has_pending_or_uncertain = any(d.get("status") in {"pending", "uncertain"} for d in deliveries)
        all_succeeded = bool(deliveries) and all(d.get("status") == "succeeded" for d in deliveries)
        return {
            "ok": True,
            "outcome": "uncertain" if has_pending_or_uncertain else ("succeeded" if all_succeeded else "failed"),
            "provider": "twilio",
            "delivery_ids": [d["id"] for d in deliveries],
            "provider_object_ids": [d.get("provider_message_sid") for d in deliveries if d.get("provider_message_sid")],
            "provider_receipt_is_delivery_outcome": False,
            "reconciliation_required": has_pending_or_uncertain,
            "deliveries": deliveries,
        }

    kernel.register(ActionContract(**CRM_MESSAGE_ACTION_CONTRACT_SPEC, handler=execute_message))
    return CRM_MESSAGE_ACTION_CONTRACT_SPEC["action_type"]
