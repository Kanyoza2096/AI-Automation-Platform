import pytest
import asyncio
from services.action_kernel import ActionContract, ActionKernel, ActionIntent, build_default_action_kernel


def test_action_intent_requires_workspace_and_idempotency():
    try:
        ActionIntent("", "x", idempotency_key="k").validate()
        assert False
    except ValueError:
        pass


def test_kernel_requires_approval_for_high_risk_before_side_effect():
    calls = []
    k = ActionKernel(allow_in_memory=True)
    k.register(ActionContract("danger", risk_level="high", requires_approval=True, handler=lambda p, i: calls.append(1) or {"ok": True}))
    out = asyncio.run(k.execute(workspace_id="ws", action_type="danger", risk_level="high", parameters={}, idempotency_key="x"))
    assert out["status"] == "approval_required"
    assert calls == []


def test_kernel_is_idempotent_through_execution_ledger():
    calls = []
    k = ActionKernel(allow_in_memory=True)
    k.register(ActionContract("safe", risk_level="low", handler=lambda p, i: calls.append(1) or {"ok": True, "id": "r1"}))
    a = asyncio.run(k.execute(workspace_id="ws", action_type="safe", parameters={"x": 1}, idempotency_key="same"))
    b = asyncio.run(k.execute(workspace_id="ws", action_type="safe", parameters={"x": 1}, idempotency_key="same"))
    assert a["status"] == b["status"] == "succeeded"
    assert calls == [1]
    assert b["replayed"] is True


def test_crm_contracts_are_registered_and_typed():
    k = build_default_action_kernel(allow_in_memory=True)
    assert set(["crm_add_contact", "crm_add_deal", "crm_schedule_followup", "crm_add_meeting"]).issubset(k._contracts)
    assert k._contracts["crm_add_deal"].requires_approval is True
    assert k._contracts["crm_add_deal"].risk_level == "high"


def test_async_enqueue_does_not_execute_side_effect():
    calls = []
    k = ActionKernel(allow_in_memory=True)
    k.register(ActionContract("safe_async", risk_level="low", handler=lambda p, i: calls.append(1) or {"ok": True}))
    out = asyncio.run(k.enqueue(workspace_id="ws", action_type="safe_async", parameters={"x": 1}, idempotency_key="q"))
    assert out["status"] == "pending"
    assert out["accepted"] is True
    assert calls == []


def test_async_enqueue_high_risk_requires_approval_then_approval_releases():
    k = ActionKernel(allow_in_memory=True)
    k.register(ActionContract("danger_async", risk_level="high", requires_approval=True, handler=lambda p, i: {"ok": True}))
    out = asyncio.run(k.enqueue(workspace_id="ws", action_type="danger_async", risk_level="high", idempotency_key="q"))
    assert out["status"] == "approval_required"
    approved = asyncio.run(k.approve(workspace_id="ws", intent_id=out["intent_id"]))
    assert approved["status"] == "pending"


def test_worker_processes_claimed_row_through_kernel():
    calls = []
    k = ActionKernel(allow_in_memory=True)
    k.register(ActionContract("worker_safe", risk_level="low", handler=lambda p, i: calls.append(p["x"]) or {"ok": True, "value": 7, "object_id": "worker-object-7"}))
    queued = asyncio.run(k.enqueue(workspace_id="ws", action_type="worker_safe", parameters={"x": 9}, idempotency_key="worker-1"))
    row = k._memory_intents[queued["intent_id"]]
    result = asyncio.run(k.process_outbox_row(row))
    assert result["status"] == "succeeded"
    assert calls == [9]

@pytest.mark.asyncio
async def test_facebook_message_is_registered_as_governed_action(monkeypatch):
    from services.action_kernel import build_default_action_kernel

    class FB:
        def send_message(self, recipient, message):
            return True

    import services.facebook
    monkeypatch.setattr(services.facebook, "facebook", FB())
    kernel = build_default_action_kernel(None, allow_in_memory=True)
    result = await kernel.execute(
        workspace_id="ws-1",
        action_type="facebook_message",
        parameters={"recipient_id": "r1", "message": "hello"},
        risk_level="medium",
        approved=False,
    )
    # The mocked legacy Facebook transport returns only a boolean and no
    # provider object/message ID. Under the receipt contract that is uncertain,
    # not fabricated success.
    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True
