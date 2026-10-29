from plugins.industries.crm.repository import CRMRepository
from scheduler.crm_message_reconciliation_scheduler import CRMMessageReconciliationScheduler
from services.reconciliation_orchestrator import ReconciliationOrchestrator

class FakeTwilio:
    def get_message(self, reference):
        return {"sid": reference, "status": "delivered"}

def test_crm_message_scheduler_requires_distributed_lock_and_routes_reconciliation():
    repo = CRMRepository()
    delivery = repo.create_message_delivery("crm-a", {
        "contact_id": "ctc-1", "to_number": "+265888000001", "body": "Hi",
        "idempotency_key": "k", "provider_message_sid": "SM1", "status": "uncertain",
    })
    scheduler = CRMMessageReconciliationScheduler(repo, FakeTwilio(), redis_client=None, orchestrator=ReconciliationOrchestrator(), interval_seconds=30)
    assert scheduler.run_once() is None
    assert repo.list_message_reconciliation_evidence("crm-a", delivery.id) == []
