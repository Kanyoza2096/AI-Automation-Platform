import asyncio
from services.execution_ledger import ExecutionLedger
from services.external_action_executor import ExternalActionExecutor


def test_universal_executor_replays_success_without_second_side_effect():
    ledger = ExecutionLedger(allow_in_memory=True)
    executor = ExternalActionExecutor(ledger)
    calls = []

    async def op():
        calls.append(1)
        return {"ok": True, "id": "provider-1"}

    first = asyncio.run(executor.execute(workspace_id="ws", action="email.send", idempotency_key="k1", provider="mail", operation=op))
    second = asyncio.run(executor.execute(workspace_id="ws", action="email.send", idempotency_key="k1", provider="mail", operation=op))
    assert first["status"] == "succeeded"
    assert second["replayed"] is True
    assert len(calls) == 1


def test_universal_executor_marks_transport_failure_uncertain():
    ledger = ExecutionLedger(allow_in_memory=True)
    executor = ExternalActionExecutor(ledger)

    def op():
        raise TimeoutError("provider timeout")

    result = asyncio.run(executor.execute(workspace_id="ws", action="payment.capture", idempotency_key="k2", provider="payments", operation=op))
    assert result["status"] == "uncertain"
