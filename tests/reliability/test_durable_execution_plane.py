from pathlib import Path
from services.execution_ledger import ExecutionLedger

ROOT = Path(__file__).resolve().parents[2]


def test_execution_idempotency_is_tenant_scoped_in_migration():
    sql = (ROOT / "migrations/036_durable_execution_plane.sql").read_text()
    assert "ON external_execution_ledger (workspace_id, idempotency_key)" in sql
    assert "ON CONFLICT (workspace_id, idempotency_key) DO NOTHING" in sql
    assert "WHERE workspace_id = trim(p_workspace_id)" in sql


def test_lease_renewal_is_ownership_fenced():
    ledger = ExecutionLedger(allow_in_memory=True)
    record = ledger.begin(workspace_id="ws", action="publish", idempotency_key="renew", provider="x")
    renewed = ledger.renew(record, lease_seconds=60)
    assert renewed.locked_by == ledger.worker_id
    assert renewed.locked_until


def test_lease_renewal_rejects_stale_worker():
    ledger_a = ExecutionLedger(allow_in_memory=True)
    ledger_b = ExecutionLedger(allow_in_memory=True)
    ledger_b._memory = ledger_a._memory
    record = ledger_a.begin(workspace_id="ws", action="publish", idempotency_key="renew-race", provider="x")
    try:
        ledger_b.renew(record, lease_seconds=60)
    except RuntimeError as exc:
        assert "ownership" in str(exc)
    else:
        raise AssertionError("stale worker renewed another worker's lease")
