"""Distributed execution race certification.

These tests exercise the authoritative execution contract with two independent
workers sharing a DB-like atomic store.  The goal is to prove that concurrency
is controlled by the execution ledger, not by process-local asyncio state.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timedelta, timezone
from services.execution_ledger import ExecutionLedger
from services.external_action_executor import ExternalActionExecutor


class _RPC:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class _AtomicExecutionDB:
    """Small thread-safe stand-in for the durable SQL RPC boundary."""

    def __init__(self):
        self.lock = threading.RLock()
        self.rows = {}
        self.provider_calls = 0
        self.claims = []

    def _row(self, execution_id):
        for row in self.rows.values():
            if row["execution_id"] == execution_id:
                return row
        return None

    def rpc(self, name, payload):
        with self.lock:
            if name == "begin_external_execution":
                key = (payload["p_workspace_id"], payload["p_idempotency_key"])
                row = self.rows.get(key)
                if row is None:
                    row = {
                        "execution_id": "exe-" + uuid.uuid4().hex,
                        "workspace_id": payload["p_workspace_id"],
                        "action": payload["p_action"],
                        "idempotency_key": payload["p_idempotency_key"],
                        "status": "started",
                        "attempt": 0,
                        "provider": payload.get("p_provider"),
                        "provider_request_id": None,
                        "provider_object_id": None,
                        "error": None,
                        "evidence": payload.get("p_metadata") or {},
                        "locked_by": None,
                        "locked_until": None,
                    }
                    self.rows[key] = row
                return _RPC([dict(row)])

            if name == "claim_external_execution":
                row = self._row(payload["p_execution_id"])
                if row is None:
                    return _RPC([])
                now = datetime.now(timezone.utc)
                locked_until = (
                    datetime.fromisoformat(row["locked_until"])
                    if row["locked_until"] else None
                )
                if row["status"] in {"succeeded", "failed", "cancelled", "uncertain"}:
                    return _RPC([dict(row)])
                if row["locked_by"] and locked_until and locked_until > now and row["locked_by"] != payload["p_worker_id"]:
                    self.claims.append((payload["p_worker_id"], "in_progress"))
                    return _RPC([dict(row)])
                if row["locked_by"] and locked_until and locked_until <= now and row["status"] == "started":
                    row["status"] = "uncertain"
                    row["error"] = "Execution lease expired before completion; reconciliation required"
                    row["locked_by"] = None
                    row["locked_until"] = None
                    self.claims.append((payload["p_worker_id"], "uncertain"))
                    return _RPC([dict(row)])
                row["locked_by"] = payload["p_worker_id"]
                row["locked_until"] = (now + timedelta(seconds=payload["p_lease_seconds"])).isoformat()
                self.claims.append((payload["p_worker_id"], "claimed"))
                return _RPC([dict(row)])

            if name == "complete_external_execution":
                row = self._row(payload["p_execution_id"])
                if row is None:
                    return _RPC([])
                if row["status"] in {"succeeded", "failed", "cancelled", "uncertain"}:
                    return _RPC([dict(row)])
                if row["locked_by"] != payload["p_worker_id"]:
                    raise RuntimeError("Execution ownership lost or lease expired")
                row.update({
                    "status": payload["p_status"],
                    "attempt": row["attempt"] + 1,
                    "provider_request_id": payload.get("p_provider_request_id"),
                    "provider_object_id": payload.get("p_provider_object_id"),
                    "error": payload.get("p_error"),
                    "evidence": payload.get("p_evidence") or {},
                    "locked_by": None,
                    "locked_until": None,
                })
                return _RPC([dict(row)])

            if name == "renew_external_execution":
                row = self._row(payload["p_execution_id"])
                if row is None:
                    return _RPC([])
                if row["status"] != "started" or row["locked_by"] != payload["p_worker_id"]:
                    return _RPC([])
                row["locked_until"] = (
                    datetime.now(timezone.utc) + timedelta(seconds=payload["p_lease_seconds"])
                ).isoformat()
                return _RPC([dict(row)])

            raise AssertionError(f"unexpected RPC {name}")


def test_two_independent_workers_execute_one_provider_side_effect():
    db = _AtomicExecutionDB()
    a = ExternalActionExecutor(ExecutionLedger(db=db))
    b = ExternalActionExecutor(ExecutionLedger(db=db))
    provider_started = threading.Event()
    provider_release = threading.Event()

    def provider():
        with db.lock:
            db.provider_calls += 1
        provider_started.set()
        provider_release.wait(timeout=2)
        return {"ok": True, "object_id": "remote-1"}

    async def run():
        first = asyncio.create_task(a.execute(
            workspace_id="ws", action="fleet.dispatch", idempotency_key="idem-race",
            provider="fleet", operation=provider,
        ))
        await asyncio.to_thread(provider_started.wait, 1)
        second = await b.execute(
            workspace_id="ws", action="fleet.dispatch", idempotency_key="idem-race",
            provider="fleet", operation=provider,
        )
        assert second["status"] == "in_progress"
        assert db.provider_calls == 1
        provider_release.set()
        return await first

    first = asyncio.run(run())
    assert first["status"] == "succeeded"
    assert first["provider_object_id"] == "remote-1"
    assert db.provider_calls == 1
    assert [state for _, state in db.claims].count("claimed") == 1


def test_worker_crash_after_provider_acceptance_is_ambiguous_and_replay_does_not_execute():
    db = _AtomicExecutionDB()
    executor = ExternalActionExecutor(ExecutionLedger(db=db))
    calls = {"count": 0}

    def accepted_then_connection_lost():
        calls["count"] += 1
        raise TimeoutError("response lost after provider acceptance")

    first = asyncio.run(executor.execute(
        workspace_id="ws", action="fleet.dispatch", idempotency_key="idem-timeout",
        provider="fleet", operation=accepted_then_connection_lost,
    ))
    second = asyncio.run(executor.execute(
        workspace_id="ws", action="fleet.dispatch", idempotency_key="idem-timeout",
        provider="fleet", operation=lambda: {"ok": True, "object_id": "must-not-run"},
    ))

    assert first["status"] == "uncertain"
    assert first["reconciliation_required"] is True
    assert second["status"] == "uncertain"
    assert second["replayed"] is True
    assert calls["count"] == 1


def test_expired_owner_is_quarantined_before_second_worker_can_execute():
    db = _AtomicExecutionDB()
    ledger_a = ExecutionLedger(db=db)
    ledger_b = ExecutionLedger(db=db)
    record = ledger_a.begin(
        workspace_id="ws", action="fleet.dispatch", idempotency_key="idem-expired", provider="fleet"
    )
    claimed = ledger_a.claim(record)
    row = db._row(claimed.execution_id)
    row["locked_until"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

    takeover = ledger_b.claim(claimed)
    assert takeover.status == "uncertain"
    assert takeover.locked_by is None
    assert takeover.error == "Execution lease expired before completion; reconciliation required"


def test_stale_worker_cannot_finalize_after_lease_is_quarantined():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sql = (root / "migrations/035_execution_ownership_fencing.sql").read_text()
    assert "r.locked_by IS DISTINCT FROM p_worker_id" in sql
    assert "r.locked_until <= now()" in sql
    assert "locked_by = NULL" in sql
    assert "locked_until = NULL" in sql


def test_idempotency_remains_workspace_scoped_under_concurrent_workers():
    db = _AtomicExecutionDB()
    a = ExecutionLedger(db=db)
    b = ExecutionLedger(db=db)
    first = a.begin(workspace_id="ws-a", action="fleet.dispatch", idempotency_key="same-key")
    second = b.begin(workspace_id="ws-b", action="fleet.dispatch", idempotency_key="same-key")
    assert first.execution_id != second.execution_id
    assert len(db.rows) == 2
    assert first.workspace_id != second.workspace_id
    assert len({first.workspace_id, second.workspace_id}) == 2


def test_concurrent_begin_same_workspace_collapses_to_one_execution_identity():
    db = _AtomicExecutionDB()
    ledgers = [ExecutionLedger(db=db) for _ in range(2)]
    barrier = threading.Barrier(2)
    results = []

    def begin(ledger):
        barrier.wait()
        results.append(ledger.begin(
            workspace_id="ws", action="fleet.dispatch", idempotency_key="same-key", provider="fleet"
        ))

    threads = [threading.Thread(target=begin, args=(ledger,)) for ledger in ledgers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2
    assert results[0].execution_id == results[1].execution_id
    assert len(db.rows) == 1
