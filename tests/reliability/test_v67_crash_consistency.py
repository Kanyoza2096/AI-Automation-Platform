"""V67.6 crash-consistency and execution-lease tests.

The critical invariant is conservative ambiguity: if a worker can no longer
prove that a provider side effect was durably recorded, recovery must observe
UNCERTAIN rather than manufacture a retryable failure or execute a duplicate.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.execution_ledger import ExecutionLedger, ExecutionRecord
from services.external_action_executor import ExternalActionExecutor

ROOT = Path(__file__).resolve().parents[2]


def test_expired_in_memory_execution_lease_becomes_uncertain_like_postgres():
    ledger = ExecutionLedger(allow_in_memory=True)
    first = ledger.begin(
        workspace_id="ws", action="crm.add_contact", idempotency_key="crash-window", provider="crm"
    )
    expired = ExecutionRecord(
        first.execution_id, first.workspace_id, first.action, first.idempotency_key,
        "started", first.attempt, first.provider, None, None, None, {},
        "dead-worker", (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    ledger._memory[first.idempotency_key] = expired

    second_worker = ExecutionLedger(allow_in_memory=True)
    # Share the durable-memory model for this unit test; the worker identity is
    # intentionally different, matching a post-crash replacement worker.
    second_worker._memory = ledger._memory
    current = second_worker.claim(expired)

    assert current.status == "uncertain"
    assert current.locked_by is None
    assert current.evidence["lease_expired"] is True
    assert "reconciliation required" in (current.error or "").lower()


def test_live_execution_lease_is_renewed_by_heartbeat():
    class HeartbeatLedger(ExecutionLedger):
        def __init__(self):
            super().__init__(allow_in_memory=True)
            self.renewals = 0

        def renew(self, record, *, lease_seconds=300):
            self.renewals += 1
            return super().renew(record, lease_seconds=lease_seconds)

    ledger = HeartbeatLedger()
    executor = ExternalActionExecutor(ledger)
    record = ledger.begin(
        workspace_id="ws", action="crm.add_contact", idempotency_key="heartbeat", provider="crm"
    )
    record = ledger.claim(record)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(executor._execution_heartbeat(record, stop, interval=0.01))
        await asyncio.sleep(0.04)
        stop.set()
        await task

    asyncio.run(run())
    assert ledger.renewals >= 1


def test_heartbeat_failure_does_not_claim_remote_cancellation_or_success():
    class BrokenRenewLedger(ExecutionLedger):
        def renew(self, record, *, lease_seconds=300):
            raise RuntimeError("database unavailable")

    ledger = BrokenRenewLedger(allow_in_memory=True)
    executor = ExternalActionExecutor(ledger)
    record = ledger.begin(
        workspace_id="ws", action="crm.add_contact", idempotency_key="heartbeat-failure", provider="crm"
    )
    record = ledger.claim(record)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(executor._execution_heartbeat(record, stop, interval=0.01))
        await asyncio.sleep(0.03)
        await task

    asyncio.run(run())
    # The ledger remains non-terminal: the failed heartbeat must not invent a
    # successful or cancelled provider state.
    current = ledger._memory[record.idempotency_key]
    assert current.status == "started"


def test_execution_renew_rpc_is_present_and_fenced():
    sql = (ROOT / "migrations/036_durable_execution_plane.sql").read_text()
    assert "CREATE OR REPLACE FUNCTION renew_external_execution" in sql
    assert "p_execution_id" in sql
    assert "p_worker_id" in sql
    assert "locked_by = p_worker_id" in sql
    assert "locked_until > now()" in sql


def test_completion_remains_the_terminal_crash_boundary():
    sql = (ROOT / "migrations/035_execution_ownership_fencing.sql").read_text()
    assert "r.locked_by IS DISTINCT FROM p_worker_id" in sql
    assert "r.locked_until <= now()" in sql
    assert "Execution ownership lost or lease expired" in sql
