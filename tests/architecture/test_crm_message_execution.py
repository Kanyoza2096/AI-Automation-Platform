import asyncio

from plugins.industries.crm.repository import CRMRepository
from services.action_kernel import build_default_action_kernel
from services.sector_crm_message_reconciliation import CRMMessageReconciliationService


class FakeTwilio:
    def __init__(self, status="sent"):
        self.status = status
        self.calls = []

    def send_sms(self, *, from_, to, body):
        self.calls.append((from_, to, body))
        return {"ok": True, "provider": "twilio", "status": "succeeded", "outcome": "succeeded", "provider_object_id": "SM123", "provider_request_id": "req-1"}

    def get_message(self, reference):
        self.calls.append(("read", reference))
        return {"sid": reference, "status": self.status}


def _repo_with_contact():
    repo = CRMRepository()
    repo.add_contact("crm-a", {"contact_name": "Jane", "phone": "+265888000001", "status": "lead"})
    return repo


def test_crm_message_execution_is_uncertain_until_twilio_delivery_reconciliation():
    repo = _repo_with_contact()
    twilio = FakeTwilio()
    kernel = build_default_action_kernel(allow_in_memory=True, crm_repository=repo, twilio_adapter=twilio)
    result = asyncio.run(kernel.execute(
        workspace_id="crm-a",
        action_type="crm_send_message",
        parameters={"message": "Hello", "status": "lead", "from_": "+26580000000", "reference": "followup-1"},
        risk_level="high", decision_id="dec-1", source="test",
    ))
    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True
    assert result["output"]["provider_receipt_is_delivery_outcome"] is False
    assert len(twilio.calls) == 1
    deliveries = repo.list_reconciliation_required_message_deliveries("crm-a")
    assert len(deliveries) == 1
    assert deliveries[0].provider_message_sid == "SM123"


def test_crm_message_reconciliation_nonterminal_preserves_uncertainty():
    repo = _repo_with_contact()
    twilio = FakeTwilio("sent")
    delivery = repo.create_message_delivery("crm-a", {
        "contact_id": "ctc-x", "to_number": "+265888000001", "body": "Hi",
        "idempotency_key": "k1", "provider_message_sid": "SM1", "status": "uncertain",
    })
    result = CRMMessageReconciliationService(repo, twilio).reconcile(workspace_id="crm-a", delivery_id=delivery.id)
    assert result["terminal"] is False
    assert result["status"] == "in_progress"
    assert repo.get_message_delivery("crm-a", delivery.id).status == "uncertain"
    assert repo.list_message_reconciliation_evidence("crm-a", delivery.id)[0].verification_state == "provider_observed_nonterminal"


def test_crm_message_reconciliation_delivered_records_terminal_state():
    repo = _repo_with_contact()
    twilio = FakeTwilio("delivered")
    delivery = repo.create_message_delivery("crm-a", {
        "contact_id": "ctc-x", "to_number": "+265888000001", "body": "Hi",
        "idempotency_key": "k2", "provider_message_sid": "SM2", "status": "uncertain",
    })
    result = CRMMessageReconciliationService(repo, twilio).reconcile(workspace_id="crm-a", delivery_id=delivery.id)
    assert result["terminal"] is True
    assert result["status"] == "succeeded"
    assert repo.get_message_delivery("crm-a", delivery.id).status == "succeeded"
    assert repo.list_message_reconciliation_evidence("crm-a", delivery.id)[0].verification_state == "verified_provider_terminal"


def test_crm_message_reconciliation_missing_provider_reference_goes_to_operator_review():
    repo = _repo_with_contact()
    twilio = FakeTwilio()
    delivery = repo.create_message_delivery("crm-a", {
        "contact_id": "ctc-x", "to_number": "+265888000001", "body": "Hi",
        "idempotency_key": "k3", "status": "uncertain",
    })
    result = CRMMessageReconciliationService(repo, twilio).reconcile(workspace_id="crm-a", delivery_id=delivery.id)
    assert result["status"] == "operator_review"
    assert twilio.calls == []


def test_crm_message_reconciliation_is_workspace_scoped():
    repo = _repo_with_contact()
    twilio = FakeTwilio("delivered")
    delivery = repo.create_message_delivery("crm-a", {
        "contact_id": "ctc-x", "to_number": "+265888000001", "body": "Hi",
        "idempotency_key": "k4", "provider_message_sid": "SM4", "status": "uncertain",
    })
    try:
        CRMMessageReconciliationService(repo, twilio).reconcile(workspace_id="crm-b", delivery_id=delivery.id)
    except ValueError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("cross-workspace CRM reconciliation must fail")
