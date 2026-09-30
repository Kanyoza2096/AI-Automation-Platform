"""Recovery certification: reconcile first, never duplicate the external side effect."""

from services.execution_ledger import ExecutionLedger
from services.execution_recovery_orchestrator import ExecutionRecoveryOrchestrator


def _uncertain(ledger):
    started = ledger.begin(
        workspace_id="ws-recovery",
        action="warehouse.notify_dispatch",
        idempotency_key="recovery-SHP-900",
        provider="synthetic",
    )
    return ledger.complete(
        started,
        status="uncertain",
        error="transport response lost after provider acceptance",
        evidence={"side_effect_may_exist": True},
    )


def test_authoritative_success_converges_without_reexecution():
    ledger = ExecutionLedger(None, allow_in_memory=True)
    record = _uncertain(ledger)
    provider_calls = []

    class Reconciler:
        def reconcile(self, **kwargs):
            provider_calls.append(kwargs)
            return {
                "status": "published",
                "authoritative": True,
                "provider_object_id": "external-900",
                "evidence": "provider lookup confirmed object",
            }

    result = ExecutionRecoveryOrchestrator(ledger).recover(record, Reconciler())
    current = ledger._memory[record.idempotency_key]

    assert result.status == "succeeded"
    assert result.side_effect_reexecuted is False
    assert result.reconciliation_required is False
    assert current.provider_object_id == "external-900"
    assert ledger.operational_state(current).health == "healthy"
    assert provider_calls == [{
        "idempotency_key": "recovery-SHP-900",
        "execution_id": record.execution_id,
        "provider_object_id": None,
    }]


def test_authoritative_absence_closes_recovery_debt_without_retrying():
    ledger = ExecutionLedger(None, allow_in_memory=True)
    record = _uncertain(ledger)
    executions = []

    class Reconciler:
        def reconcile(self, **kwargs):
            return {"status": "not_found", "authoritative": True, "evidence": "provider confirmed absence"}

    result = ExecutionRecoveryOrchestrator(ledger).recover(record, Reconciler())
    current = ledger._memory[record.idempotency_key]

    assert result.status == "failed"
    assert result.side_effect_reexecuted is False
    assert current.status == "failed"
    assert ledger.operational_state(current).reconciliation_required is False
    assert executions == []


def test_inconclusive_reconciliation_keeps_execution_frozen():
    ledger = ExecutionLedger(None, allow_in_memory=True)
    record = _uncertain(ledger)

    class Reconciler:
        def reconcile(self, **kwargs):
            return {"status": "unknown", "authoritative": False, "evidence": "provider unavailable"}

    result = ExecutionRecoveryOrchestrator(ledger).recover(record, Reconciler())
    current = ledger._memory[record.idempotency_key]
    state = ledger.operational_state(current)

    assert result.status == "uncertain"
    assert result.side_effect_reexecuted is False
    assert result.reconciliation_required is True
    assert state.incident_required is True
    assert state.reconciliation_request()["execution_id"] == record.execution_id


def test_already_healthy_execution_is_observation_only():
    ledger = ExecutionLedger(None, allow_in_memory=True)
    started = ledger.begin(
        workspace_id="ws-recovery",
        action="warehouse.notify_dispatch",
        idempotency_key="recovery-SHP-901",
        provider="synthetic",
    )
    record = ledger.complete(started, status="succeeded", provider_object_id="external-901")

    class Reconciler:
        def reconcile(self, **kwargs):
            raise AssertionError("healthy execution must not invoke reconciliation")

    result = ExecutionRecoveryOrchestrator(ledger).recover(record, Reconciler())
    assert result.status == "succeeded"
    assert result.side_effect_reexecuted is False
    assert result.reconciliation_required is False
