"""V67.2 failure-injection tests for the real recovery boundary.

These tests intentionally exercise ugly failure modes rather than happy-path
recovery: provider uncertainty, receipt persistence failure, stale ownership,
and worker-level exceptions outside the normal kernel result path.
"""
import asyncio
from types import SimpleNamespace

from services.execution_ledger import ExecutionLedger
from services.external_action_executor import ExternalActionExecutor


class _Rpc:
    def __init__(self, data=True):
        self.data = data
    def execute(self):
        return self


class _Table:
    def __init__(self, db, name):
        self.db, self.name = db, name
    def upsert(self, payload, **kwargs):
        self.db.calls.append((self.name, "upsert", payload))
        return self
    def update(self, payload):
        self.db.calls.append((self.name, "update", payload))
        return self
    def eq(self, *args):
        return self
    def execute(self):
        return _Rpc(True)


class _DB:
    def __init__(self):
        self.calls = []
    def table(self, name):
        return _Table(self, name)
    def rpc(self, name, payload):
        self.calls.append((name, "rpc", payload))
        return _Rpc(True)


def test_receipt_persistence_failure_becomes_uncertain_not_retryable_success():
    class BrokenLedger(ExecutionLedger):
        def complete(self, *args, **kwargs):
            raise RuntimeError("ledger unavailable")

    ledger = BrokenLedger(allow_in_memory=True)
    executor = ExternalActionExecutor(ledger)
    calls = []

    def provider():
        calls.append(1)
        return {"ok": True, "id": "provider-1"}

    result = asyncio.run(executor.execute(
        workspace_id="ws", action="crm.add_contact", idempotency_key="receipt-fail",
        provider="crm", operation=provider,
    ))
    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True
    assert calls == [1]


def test_same_idempotency_key_replays_without_second_provider_call():
    ledger = ExecutionLedger(allow_in_memory=True)
    executor = ExternalActionExecutor(ledger)
    calls = []

    def provider():
        calls.append(1)
        return {"ok": True, "id": "provider-1"}

    first = asyncio.run(executor.execute(
        workspace_id="ws", action="crm.add_contact", idempotency_key="dup",
        provider="crm", operation=provider,
    ))
    second = asyncio.run(executor.execute(
        workspace_id="ws", action="crm.add_contact", idempotency_key="dup",
        provider="crm", operation=provider,
    ))
    assert first["status"] == "succeeded"
    assert second["replayed"] is True
    assert len(calls) == 1


def test_worker_exception_enters_v67_reconciliation_path(monkeypatch):
    from services.action_outbox_worker import ActionOutboxWorker

    db = _DB()

    class ExplodingKernel:
        _contracts = {
            "crm_add_contact": SimpleNamespace(
                autonomous_retryable=True, irreversible=False,
                requires_approval=False, reconciler=None,
            )
        }
        async def process_outbox_row(self, row):
            raise RuntimeError("worker-side crash before normal result")

    monkeypatch.setattr(
        "services.action_kernel.build_default_action_kernel",
        lambda db, allow_in_memory=False: ExplodingKernel(),
    )

    row = {
        "intent_id": "act-1", "workspace_id": "ws", "action_type": "crm_add_contact",
        "lease_token": "lease-1", "attempts": 1, "status": "processing",
        "correlation_id": "corr-1", "execution_id": "exe-1",
    }
    result = asyncio.run(ActionOutboxWorker(db=db).process_row(row))
    assert result["status"] == "reconciliation_required"
    assert "recovery" in result
    assert result["recovery"]["status"] == "review_required"
    assert any(c[0] == "finalize_action_outbox" for c in db.calls)
    assert any(c[0] == "autonomous_exceptions" for c in db.calls)
    assert not any(c[0] == "release_action_outbox_for_recovery" for c in db.calls)


def test_release_function_is_the_race_guard_not_python_state():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    sql = (root / "migrations/057_v67_recovery_release_hardening.sql").read_text()
    assert "FOR UPDATE" in sql
    assert "v_attempts >= v_max_attempts" in sql
    assert "v_exception_intent <> p_intent_id" in sql
    assert "status = 'failed'" in sql
    assert "status = 'pending'" in sql
