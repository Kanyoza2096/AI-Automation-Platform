from types import SimpleNamespace

from services.execution_operational_state import project_execution


def record(status="succeeded", object_id="provider-1", error=None):
    return SimpleNamespace(
        execution_id="exe-1", workspace_id="ws-1", action="external_action",
        idempotency_key="idem-1", status=status, attempt=1,
        provider="synthetic", provider_object_id=object_id, error=error,
    )


def test_success_projects_to_healthy_without_reconciliation_or_incident():
    state = project_execution(record())
    assert state.status == "succeeded"
    assert state.health == "healthy"
    assert state.reconciliation_required is False
    assert state.incident_required is False
    assert state.telemetry()["provider_object_id"] == "provider-1"


def test_uncertain_projects_to_single_degraded_operational_truth():
    state = project_execution(record("uncertain", object_id=None, error="timeout"))
    assert state.status == "uncertain"
    assert state.health == "degraded"
    assert state.reconciliation_required is True
    assert state.incident_required is True
    assert state.reconciliation_request()["execution_id"] == "exe-1"
    assert state.incident_signal()["correlation_id"] == "exe-1"


def test_failed_creates_incident_but_does_not_request_provider_reconciliation():
    state = project_execution(record("failed", object_id=None, error="rejected"))
    assert state.status == "failed"
    assert state.incident_required is True
    assert state.reconciliation_required is False


def test_missing_receipt_cannot_be_reported_as_healthy_success():
    state = project_execution(record("succeeded", object_id=None))
    assert state.status == "uncertain"
    telemetry = state.telemetry()
    assert telemetry["health"] == "degraded"
    assert telemetry["reconciliation_required"] is True


def test_reconciliation_and_incident_share_same_execution_identity():
    state = project_execution(record("uncertain", object_id="maybe-1"))
    reconciliation = state.reconciliation_request()
    incident = state.incident_signal()
    assert reconciliation["execution_id"] == incident["correlation_id"] == state.execution_id
    assert reconciliation["idempotency_key"] == incident["evidence"]["idempotency_key"]


def test_cancelled_is_not_an_incident_by_itself():
    state = project_execution(record("cancelled", object_id=None, error="operator_cancelled"))
    assert state.health == "healthy"
    assert state.incident_required is False
    assert state.reconciliation_required is False


def test_execution_ledger_exposes_the_same_canonical_projection():
    from services.execution_ledger import ExecutionLedger
    ledger = ExecutionLedger(allow_in_memory=True)
    record = ledger.begin(workspace_id="ws-1", action="external_action", idempotency_key="idem-ledger", provider="synthetic")
    record = ledger.complete(record, status="uncertain", error="timeout")
    state = ledger.operational_state(record)
    assert state.execution_id == record.execution_id
    assert state.status == "uncertain"
    assert state.reconciliation_required is True
    assert state.incident_required is True
