"""V67.4 adversarial concurrency tests.

These tests model the dangerous races explicitly: a slow provider must not lose
its outbox lease, stale tokens must not renew/finalize, and a live execution owner
must be reported as in-progress rather than incorrectly finalized.
"""
import asyncio
from types import SimpleNamespace

from services.action_outbox_worker import ActionOutboxWorker


class _Rpc:
    def __init__(self, data=True):
        self.data = data

    def execute(self):
        return self


class _LeaseDB:
    def __init__(self, *, renew_results=None):
        self.calls = []
        self.renew_results = list(renew_results or [True])

    def rpc(self, name, payload):
        self.calls.append((name, payload))
        if name == "renew_action_outbox_lease":
            return _Rpc(self.renew_results.pop(0) if self.renew_results else True)
        return _Rpc(True)


def test_slow_execution_is_heartbeat_renewed_before_lease_can_expire():
    db = _LeaseDB()
    worker = ActionOutboxWorker(db=db, lease_seconds=30)
    row = {"intent_id": "act-slow", "workspace_id": "ws", "lease_token": "token"}

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(worker._heartbeat(row, stop, interval=0.01))
        await asyncio.sleep(0.035)
        stop.set()
        await task

    asyncio.run(run())
    renewals = [c for c in db.calls if c[0] == "renew_action_outbox_lease"]
    assert len(renewals) >= 2
    for _, payload in renewals:
        assert payload["p_intent_id"] == "act-slow"
        assert payload["p_workspace_id"] == "ws"
        assert payload["p_lease_token"] == "token"
        assert payload["p_lease_seconds"] == 30


def test_rejected_heartbeat_stops_renewing_and_never_changes_authority():
    db = _LeaseDB(renew_results=[False])
    worker = ActionOutboxWorker(db=db, lease_seconds=30)
    row = {"intent_id": "act-stale", "workspace_id": "ws", "lease_token": "stale"}

    async def run():
        stop = asyncio.Event()
        await worker._heartbeat(row, stop, interval=0.01)
        return stop

    stop = asyncio.run(run())
    assert stop.is_set() is False
    renewals = [c for c in db.calls if c[0] == "renew_action_outbox_lease"]
    assert len(renewals) == 1


def test_stale_token_cannot_be_used_for_finalization_or_renewal_contract():
    sql_path = __import__("pathlib").Path(__file__).resolve().parents[2] / "migrations/058_v67_outbox_lease_heartbeat.sql"
    sql = sql_path.read_text()
    assert "p_lease_token" in sql
    assert "lease_token = p_lease_token" in sql
    assert "status = 'processing'" in sql
    finalize_sql = (__import__("pathlib").Path(__file__).resolve().parents[2] / "migrations/047_action_outbox_lease_fencing.sql").read_text()
    assert "status = 'processing' AND lease_token = p_lease_token" in finalize_sql


def test_live_execution_owner_is_in_progress_not_a_terminal_outbox_status(monkeypatch):
    from services.action_kernel import ActionKernel

    class Kernel(ActionKernel):
        def __init__(self):
            pass

    kernel = Kernel()
    kernel.db = object()
    kernel._contracts = {
        "crm_add_contact": SimpleNamespace(
            requires_approval=False, irreversible=False,
            validate=lambda intent: None,
            handler=lambda params, intent: {"ok": True},
        )
    }
    kernel._build_intent = lambda **kwargs: (
        SimpleNamespace(
            workspace_id="ws", action_type="crm_add_contact", parameters={},
            risk_level="low", decision_id=None, correlation_id="corr", idempotency_key="idem", source="test",
        ),
        kernel._contracts["crm_add_contact"], "act-live",
    )
    kernel._update_intent = lambda *args, **kwargs: None

    class Executor:
        async def execute(self, **kwargs):
            return {"status": "in_progress", "execution_id": "exe-owner", "replayed": False}

    kernel.executor = Executor()

    class DB:
        def rpc(self, name, payload):
            raise AssertionError(f"terminal finalization must not be called: {name}")

    kernel.db = DB()
    row = {
        "intent_id": "act-live", "workspace_id": "ws", "action_type": "crm_add_contact",
        "parameters": {}, "risk_level": "low", "idempotency_key": "idem", "lease_token": "lease",
    }
    result = asyncio.run(kernel.process_outbox_row(row))
    assert result["status"] == "in_progress"
    assert result["execution_id"] == "exe-owner"
    assert result["reconciliation_required"] is False


def test_two_worker_race_slow_owner_keeps_outbox_claimable_state_locked_by_heartbeat():
    """Simulate worker B polling while worker A executes a slow handler.

    The fake DB uses a short wall-clock lease so the test can model expiry quickly;
    the production RPC still enforces the real 30-600 second lease bounds.
    """
    import time

    class RaceDB(_LeaseDB):
        def __init__(self):
            super().__init__()
            self.expires_at = time.monotonic() + 0.50
            self.claims = 0

        def rpc(self, name, payload):
            if name == "renew_action_outbox_lease":
                self.calls.append((name, payload))
                if time.monotonic() >= self.expires_at:
                    return _Rpc(False)
                self.expires_at = time.monotonic() + 0.50
                return _Rpc(True)
            if name == "claim_action_outbox":
                self.claims += 1
                return _Rpc(time.monotonic() >= self.expires_at)
            return _Rpc(True)

    db = RaceDB()
    worker_a = ActionOutboxWorker(db=db, lease_seconds=30)
    row = {"intent_id": "act-race", "workspace_id": "ws", "lease_token": "owner-a"}

    async def run_race():
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(worker_a._heartbeat(row, stop, interval=0.005))
        claimant_b = 0
        for _ in range(8):
            await asyncio.sleep(0.006)
            result = db.rpc("claim_action_outbox", {"p_worker_id": "worker-b"}).data
            if result:
                claimant_b += 1
        stop.set()
        await heartbeat
        return claimant_b

    claimant_b = asyncio.run(run_race())
    assert claimant_b == 0
    assert any(name == "renew_action_outbox_lease" for name, _ in db.calls)
