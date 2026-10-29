from services.reconciliation_orchestrator import ReconciliationOrchestrator
from scheduler.it_reconciliation_scheduler import ITDeploymentReconciliationScheduler


class FakeRedis:
    is_available = True

    def __init__(self):
        self.values = {}

    def set(self, key, token, ex, nx):
        if nx and key in self.values:
            return False
        self.values[key] = token
        return True

    def compare_and_delete(self, key, token):
        if self.values.get(key) == token:
            del self.values[key]
            return True
        return False


class FakeRepo:
    def list_reconciliation_required_deployments(self, workspace_id=None, limit=100):
        return []


class SpyOrchestrator(ReconciliationOrchestrator):
    def __init__(self):
        self.calls = 0

    def reconcile_it_deployments(self, **kwargs):
        self.calls += 1
        assert kwargs["limit"] == 7
        return {"discovered": 0, "reconciled": 0, "terminal": 0, "in_progress": 0, "uncertain": 0, "skipped": 0}


def test_scheduler_routes_cycles_through_canonical_orchestrator():
    spy = SpyOrchestrator()
    scheduler = ITDeploymentReconciliationScheduler(
        FakeRepo(), object(), redis_client=FakeRedis(), orchestrator=spy,
        interval_seconds=30, batch_limit=7,
    )
    result = scheduler.run_once()
    assert result["discovered"] == 0
    assert spy.calls == 1


def test_scheduler_refuses_local_only_execution_for_multi_instance_safety():
    spy = SpyOrchestrator()
    scheduler = ITDeploymentReconciliationScheduler(
        FakeRepo(), object(), redis_client=None, orchestrator=spy,
        interval_seconds=30,
    )
    assert scheduler.run_once() is None
    assert spy.calls == 0


def test_scheduler_has_no_deployment_execution_dependency():
    source = open("scheduler/it_reconciliation_scheduler.py", encoding="utf-8").read()
    assert "ActionKernel" not in source
    assert "trigger_deploy" not in source
    assert "allow_mutations=False" in source
