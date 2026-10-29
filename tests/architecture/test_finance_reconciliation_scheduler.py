from scheduler.finance_reconciliation_scheduler import FinancePaymentReconciliationScheduler
from services.reconciliation_orchestrator import ReconciliationOrchestrator
from plugins.industries.finance.repository import FinanceRepository

class FakeStripe:
    def get_payment_intent(self, reference):
        return {"id": reference, "status": "processing"}

def test_finance_scheduler_requires_distributed_lock_and_routes_reconciliation():
    repo = FinanceRepository()
    payment = repo.create_payment("fin-a", {"amount": 10, "currency": "USD", "reference": "r", "idempotency_key": "k"})
    repo.update_payment("fin-a", payment.id, provider_payment_intent_id="pi_1")
    scheduler = FinancePaymentReconciliationScheduler(repo, FakeStripe(), redis_client=None, orchestrator=ReconciliationOrchestrator(), interval_seconds=30)
    assert scheduler.run_once() is None
    assert repo.list_payment_reconciliation_evidence("fin-a", payment.id) == []
