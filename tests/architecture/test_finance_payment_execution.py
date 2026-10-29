import asyncio
from types import SimpleNamespace

from services.action_kernel import build_default_action_kernel
from plugins.industries.finance.repository import FinanceRepository
from services.sector_finance_payment_reconciliation import FinancePaymentReconciliationService


class FakeStripe:
    def __init__(self, status="processing"):
        self.status = status
        self.calls = []

    def create_payment_intent(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "provider": "stripe", "status": "succeeded", "outcome": "succeeded", "provider_object_id": "pi_123", "provider_request_id": "req_1"}

    def get_payment_intent(self, reference):
        self.calls.append(("read", reference))
        return {"id": reference, "status": self.status, "amount": 12500, "currency": "usd"}


def test_finance_payment_execution_is_uncertain_until_reconciled():
    repo = FinanceRepository()
    stripe = FakeStripe()
    kernel = build_default_action_kernel(allow_in_memory=True, finance_repository=repo, stripe_adapter=stripe)
    result = asyncio.run(kernel.execute(
        workspace_id="fin-a", action_type="finance_initiate_payment",
        parameters={"amount": 125.00, "currency": "USD", "reference": "invoice-42"},
        risk_level="critical", decision_id="dec-1", source="test",
    ))
    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True
    assert result["output"]["payment_intent_is_business_outcome"] is False
    assert stripe.calls[0]["idempotency_key"].startswith("kdo-pay-")
    payment = repo.get_payment("fin-a", result["output"]["payment_id"])
    assert payment.provider_payment_intent_id == "pi_123"


def test_finance_payment_reconciliation_nonterminal_preserves_uncertainty():
    repo = FinanceRepository()
    stripe = FakeStripe("processing")
    payment = repo.create_payment("fin-a", {"amount": 125, "currency": "USD", "reference": "r1", "idempotency_key": "k1"})
    repo.update_payment("fin-a", payment.id, provider_payment_intent_id="pi_1")
    result = FinancePaymentReconciliationService(repo, stripe).reconcile(workspace_id="fin-a", payment_id=payment.id)
    assert result["terminal"] is False
    assert result["status"] == "in_progress"
    assert result["reconciliation_required"] is True
    assert repo.get_payment("fin-a", payment.id).status == "uncertain"
    assert repo.list_payment_reconciliation_evidence("fin-a", payment.id)[0].verification_state == "provider_observed_nonterminal"


def test_finance_payment_reconciliation_succeeded_records_terminal_outcome():
    repo = FinanceRepository()
    stripe = FakeStripe("succeeded")
    payment = repo.create_payment("fin-a", {"amount": 125, "currency": "USD", "reference": "r2", "idempotency_key": "k2"})
    repo.update_payment("fin-a", payment.id, provider_payment_intent_id="pi_2")
    result = FinancePaymentReconciliationService(repo, stripe).reconcile(workspace_id="fin-a", payment_id=payment.id)
    assert result["terminal"] is True
    assert result["status"] == "succeeded"
    assert result["outcome_id"]
    assert repo.get_payment("fin-a", payment.id).status == "succeeded"
    assert repo.list_payment_reconciliation_evidence("fin-a", payment.id)[0].verification_state == "verified_provider_terminal"


def test_finance_payment_reconciliation_canceled_maps_to_failed():
    repo = FinanceRepository()
    stripe = FakeStripe("canceled")
    payment = repo.create_payment("fin-a", {"amount": 125, "currency": "USD", "reference": "r3", "idempotency_key": "k3"})
    repo.update_payment("fin-a", payment.id, provider_payment_intent_id="pi_3")
    result = FinancePaymentReconciliationService(repo, stripe).reconcile(workspace_id="fin-a", payment_id=payment.id)
    assert result["terminal"] is True
    assert result["status"] == "failed"
    assert repo.get_payment("fin-a", payment.id).status == "failed"


def test_finance_payment_reconciliation_is_workspace_scoped():
    repo = FinanceRepository()
    stripe = FakeStripe("succeeded")
    payment = repo.create_payment("fin-a", {"amount": 125, "currency": "USD", "reference": "r4", "idempotency_key": "k4"})
    repo.update_payment("fin-a", payment.id, provider_payment_intent_id="pi_4")
    try:
        FinancePaymentReconciliationService(repo, stripe).reconcile(workspace_id="fin-b", payment_id=payment.id)
    except ValueError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("cross-workspace reconciliation must fail")
