from services.execution_ledger import ExecutionLedger


def test_idempotency_key_is_stable():
    a = ExecutionLedger.make_idempotency_key("ws", "publish", "decision", "facebook")
    b = ExecutionLedger.make_idempotency_key("ws", "publish", "decision", "facebook")
    assert a == b


def test_memory_ledger_reuses_logical_execution():
    ledger = ExecutionLedger(allow_in_memory=True)
    first = ledger.begin(workspace_id="ws", action="publish", idempotency_key="exec_x", provider="facebook")
    done = ledger.complete(first, status="succeeded", provider_object_id="123")
    second = ledger.begin(workspace_id="ws", action="publish", idempotency_key="exec_x", provider="facebook")
    assert second.execution_id == done.execution_id
    assert second.status == "succeeded"
    assert second.provider_object_id == "123"


def test_uncertain_is_terminal_for_replay_until_reconciled():
    ledger = ExecutionLedger(allow_in_memory=True)
    first = ledger.begin(workspace_id="ws", action="publish", idempotency_key="exec_y", provider="facebook")
    ledger.complete(first, status="uncertain", error="timeout")
    second = ledger.begin(workspace_id="ws", action="publish", idempotency_key="exec_y", provider="facebook")
    assert second.status == "uncertain"


def test_execution_record_tracks_local_lease_identity():
    ledger = ExecutionLedger(allow_in_memory=True)
    record = ledger.begin(workspace_id="ws", action="publish", idempotency_key="exec_lease", provider="facebook")
    assert record.locked_by
    assert record.locked_until


import asyncio

def test_external_executor_normalizes_connector_result():
    from services.external_action_executor import ExternalActionExecutor
    from services.connectors.connector import ConnectorResult
    ledger = ExecutionLedger(allow_in_memory=True)
    result = asyncio.run(ExternalActionExecutor(ledger).execute(
        workspace_id="ws", action="publish", idempotency_key="exec_connector",
        provider="facebook", operation=lambda: ConnectorResult(ok=True, platform="facebook", post_id="p123")
    ))
    assert result["status"] == "succeeded"
    assert result["provider_object_id"] == "p123"


def test_ledger_claim_prevents_second_local_worker_from_executing():
    ledger_a = ExecutionLedger(allow_in_memory=True)
    ledger_b = ExecutionLedger(allow_in_memory=True)
    # Simulate a shared local ledger backing for the two workers.
    ledger_b._memory = ledger_a._memory
    first = ledger_a.begin(workspace_id="ws", action="publish", idempotency_key="exec_race", provider="facebook")
    claimed = ledger_a.claim(first)
    other = ledger_b.begin(workspace_id="ws", action="publish", idempotency_key="exec_race", provider="facebook")
    other_claim = ledger_b.claim(other)
    assert claimed.locked_by == ledger_a.worker_id
    assert other_claim.locked_by == ledger_a.worker_id
    assert other_claim.status == "started"


def test_explicit_connector_result_is_not_stringified_as_post_id():
    from services.external_action_executor import ExternalActionExecutor
    from services.connectors.connector import ConnectorResult
    import asyncio
    ledger = ExecutionLedger(allow_in_memory=True)
    result = asyncio.run(ExternalActionExecutor(ledger).execute(
        workspace_id="ws", action="publish", idempotency_key="exec_result", provider="facebook",
        operation=lambda: ConnectorResult(ok=True, platform="facebook", post_id="real-post-id")
    ))
    assert result["provider_object_id"] == "real-post-id"
    assert result["status"] == "succeeded"


def test_durable_claim_never_falls_back_to_local_memory_on_empty_rpc():
    class Resp:
        def __init__(self, data):
            self.data = data
    class RPC:
        def __init__(self, data):
            self._data = data
        def execute(self):
            return Resp(self._data)
    class DB:
        def rpc(self, name, *args, **kwargs):
            if name == "begin_external_execution":
                return RPC([{
                    "execution_id": "exe-db", "workspace_id": "ws", "action": "publish",
                    "idempotency_key": "exec-db-claim", "status": "started", "attempt": 0,
                    "provider": "facebook", "locked_by": "worker-a", "locked_until": None,
                }])
            return RPC([])

    ledger = ExecutionLedger(db=DB(), allow_in_memory=True)
    record = ledger.begin(workspace_id="ws", action="publish", idempotency_key="exec-db-claim", provider="facebook")
    import pytest
    with pytest.raises(RuntimeError, match="claim"):
        ledger.claim(record)



def test_durable_begin_preserves_canonical_text_workspace_id():
    class Resp:
        data = [{
            "execution_id": "exe-text-scope",
            "workspace_id": "workspace_alpha",
            "action": "build",
            "idempotency_key": "exec-text-scope",
            "status": "started",
            "attempt": 0,
        }]

    class RPC:
        def __init__(self, db):
            self.db = db

        def execute(self):
            return Resp()

    class DB:
        def __init__(self):
            self.args = None

        def rpc(self, name, args):
            assert name == "begin_external_execution"
            self.args = args
            return RPC(self)

    db = DB()
    ledger = ExecutionLedger(db=db)
    record = ledger.begin(
        workspace_id="workspace_alpha",
        action="build",
        idempotency_key="exec-text-scope",
    )

    assert db.args["p_workspace_id"] == "workspace_alpha"
    assert record.workspace_id == "workspace_alpha"
