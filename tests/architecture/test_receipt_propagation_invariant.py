import asyncio

from services.external_action_executor import ExternalActionExecutor
from services.execution_ledger import ExecutionLedger
from services.receipt_protocol import ProviderReceipt, assert_receipt_propagation, receipt_from_result


def test_successful_provider_receipt_is_canonical_and_authoritative():
    receipt = receipt_from_result(
        {"ok": True, "platform": "fake", "post_id": "provider-123"},
        provider="fake",
    )
    assert isinstance(receipt, ProviderReceipt)
    assert receipt.status == "succeeded"
    assert receipt.provider_object_id == "provider-123"
    assert receipt.authoritative is True


def test_success_without_provider_object_id_is_uncertain():
    receipt = receipt_from_result(
        {"ok": True, "platform": "fake"}, provider="fake"
    )
    assert receipt.status == "uncertain"
    assert receipt.provider_object_id is None
    assert receipt.error == "Provider returned success without an object ID"


def test_uncertain_observed_id_is_reconciliation_evidence_not_success():
    receipt = receipt_from_result(
        {"ok": False, "platform": "fake", "post_id": "observed-1", "outcome": "uncertain"},
        provider="fake",
    )
    assert receipt.status == "uncertain"
    assert receipt.observed_object_id == "observed-1"
    assert receipt.provider_object_id is None


def test_receipt_propagation_accepts_matching_downstream_result():
    receipt = receipt_from_result({"ok": True, "platform": "fake", "post_id": "p-1"}, provider="fake")
    assert_receipt_propagation(receipt, {"status": "succeeded", "provider_object_id": "p-1"})


def test_receipt_propagation_rejects_lost_provider_id():
    receipt = receipt_from_result({"ok": True, "platform": "fake", "post_id": "p-1"}, provider="fake")
    try:
        assert_receipt_propagation(receipt, {"status": "succeeded"})
    except AssertionError as exc:
        assert "object ID" in str(exc)
    else:
        raise AssertionError("expected receipt propagation invariant violation")


def test_receipt_propagation_rejects_uncertain_to_success_upgrade():
    receipt = receipt_from_result({"ok": True, "platform": "fake"}, provider="fake")
    try:
        assert_receipt_propagation(receipt, {"status": "succeeded"})
    except AssertionError as exc:
        assert "uncertain" in str(exc)
    else:
        raise AssertionError("expected receipt propagation invariant violation")


def test_executor_returns_first_class_receipt():
    ledger = ExecutionLedger(allow_in_memory=True)
    result = asyncio.run(ExternalActionExecutor(ledger).execute(
        workspace_id="ws",
        action="publish",
        idempotency_key="receipt-first-class",
        provider="fake",
        operation=lambda: {"ok": True, "post_id": "provider-9", "platform": "fake"},
    ))
    assert result["status"] == "succeeded"
    assert result["receipt"]["provider_object_id"] == "provider-9"
    assert result["receipt"]["status"] == "succeeded"
