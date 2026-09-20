import asyncio

from services.action_outbox_worker import ActionOutboxWorker


def test_action_outbox_workers_have_unique_fenced_identity_within_same_process():
    a = ActionOutboxWorker(db=object())
    b = ActionOutboxWorker(db=object())
    assert a.worker_id != b.worker_id
    assert a.worker_id.startswith("action-worker:")


def test_lease_heartbeat_rpc_is_fenced_by_intent_workspace_and_token():
    calls = []

    class RPC:
        def execute(self):
            calls.append(True)
            return type("R", (), {"data": True})()

    class DB:
        def rpc(self, name, payload):
            assert name == "renew_action_outbox_lease"
            assert payload == {
                "p_intent_id": "act-1",
                "p_workspace_id": "ws-1",
                "p_lease_token": "token-1",
                "p_lease_seconds": 120,
            }
            return RPC()

    worker = ActionOutboxWorker(db=DB(), lease_seconds=120)
    async def run_once_heartbeat():
        row = {"intent_id": "act-1", "workspace_id": "ws-1", "lease_token": "token-1"}
        stop = asyncio.Event()
        task = asyncio.create_task(worker._heartbeat(row, stop, interval=0))
        await asyncio.sleep(0.01)
        stop.set()
        await task

    # Keep this test focused on the fencing payload without invoking a provider.
    asyncio.run(run_once_heartbeat())
    assert calls
