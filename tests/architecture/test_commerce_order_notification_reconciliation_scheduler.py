from services.reconciliation_orchestrator import ReconciliationOrchestrator
from scheduler.commerce_order_notification_reconciliation_scheduler import CommerceOrderNotificationReconciliationScheduler

class Repo:
    def __init__(self): self.workspace_id="w"; self.delivery_id="d"; self.called=False
    def list_pending_order_notification_deliveries(self, workspace_id=None, limit=100): return []
class Twilio: pass

def test_scheduler_is_reconciliation_only_and_requires_distributed_lock():
    s=CommerceOrderNotificationReconciliationScheduler(Repo(),Twilio(),redis_client=None,orchestrator=ReconciliationOrchestrator(),interval_seconds=30)
    assert s.status()["running"] is False
    assert s.run_once() is None
    assert s.status()["run_count"] == 0
