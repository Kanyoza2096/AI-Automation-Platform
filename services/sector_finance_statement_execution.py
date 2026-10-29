from __future__ import annotations
import hashlib
from typing import Any
from services.action_kernel import ActionContract

FINANCE_STATEMENT_ACTION_CONTRACT_SPEC = {
    "action_type": "finance_send_statement",
    "risk_level": "high",
    "required_parameters": ("account_id", "to_number", "message", "from_", "reference"),
    "requires_approval": True,
    "irreversible": True,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}

def _key(workspace_id: str, account_id: str, to_number: str, message: str, reference: str) -> str:
    raw = f"{workspace_id}|{account_id}|{to_number}|{message}|{reference}".encode()
    return "kdo-fin-statement-" + hashlib.sha256(raw).hexdigest()[:48]

def register_finance_statement_action(kernel: Any, finance_repository: Any, twilio_adapter: Any) -> str:
    async def execute(parameters: dict[str, Any], intent: Any) -> dict[str, Any]:
        ws = str(intent.workspace_id)
        account_id = str(parameters["account_id"]).strip()
        to_number = str(parameters["to_number"]).strip()
        message = str(parameters["message"]).strip()
        from_ = str(parameters["from_"]).strip()
        reference = str(parameters["reference"]).strip()
        if not all((account_id, to_number, message, from_, reference)):
            raise ValueError("account_id, to_number, message, from_ and reference are required")
        account = finance_repository.get_account(ws, account_id)
        if account is None:
            raise ValueError("account not found in workspace")
        key = _key(ws, account_id, to_number, message, reference)
        existing = finance_repository.get_statement_delivery_by_idempotency_key(ws, key)
        if existing is not None:
            return {"ok": True, "outcome": "uncertain" if existing.status in {"pending", "uncertain"} else existing.status,
                    "provider": "twilio", "statement_delivery_id": existing.id,
                    "provider_object_id": existing.provider_message_sid,
                    "provider_receipt_is_business_outcome": False,
                    "reconciliation_required": existing.reconciliation_required,
                    "delivery": existing.model_dump(mode="json")}
        delivery = finance_repository.create_statement_delivery(ws, {
            "account_id": account_id, "to_number": to_number, "body": message,
            "idempotency_key": key, "provider": "twilio", "status": "pending",
            "reconciliation_required": True,
            "evidence": {"reference": reference, "intent_id": str(getattr(intent, "id", ""))},
        })
        receipt = twilio_adapter.send_sms(from_=from_, to=to_number, body=message)
        if not isinstance(receipt, dict):
            receipt = {"status": "uncertain", "outcome": "uncertain", "error": "invalid Twilio receipt"}
        provider_ref = receipt.get("provider_object_id") or receipt.get("observed_object_id") or receipt.get("id")
        provider_status = str(receipt.get("status") or receipt.get("outcome") or "unknown").lower()
        status = "failed" if provider_status == "failed" else "uncertain"
        delivery = finance_repository.update_statement_delivery(
            ws, delivery.id, provider_message_sid=str(provider_ref) if provider_ref else None,
            provider_status=provider_status, status=status, reconciliation_required=(status != "failed"),
            evidence={**delivery.evidence, "provider_receipt": receipt, "from": from_},
        )
        return {"ok": True, "outcome": status, "provider": "twilio", "statement_delivery_id": delivery.id,
                "provider_object_id": provider_ref, "provider_receipt_is_business_outcome": False,
                "reconciliation_required": delivery.reconciliation_required,
                "delivery": delivery.model_dump(mode="json")}
    kernel.register(ActionContract(**FINANCE_STATEMENT_ACTION_CONTRACT_SPEC, handler=execute))
    return FINANCE_STATEMENT_ACTION_CONTRACT_SPEC["action_type"]
