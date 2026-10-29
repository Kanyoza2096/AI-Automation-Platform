from scheduler.healthcare_patient_message_reconciliation_scheduler import HealthcarePatientMessageReconciliationScheduler
from services.reconciliation_orchestrator import ReconciliationOrchestrator
from plugins.industries.healthcare.repository import HealthcareRepository

class FakeTwilio:
    def get_message(self,sid): return {"sid":sid,"status":"delivered"}

def test_scheduler_is_reconciliation_only_and_uses_orchestrator():
    repo=HealthcareRepository()
    scheduler=HealthcarePatientMessageReconciliationScheduler(repo,FakeTwilio(),redis_client=None,orchestrator=ReconciliationOrchestrator(),interval_seconds=30)
    assert scheduler.status()["running"] is False
    assert scheduler.run_once() is None
