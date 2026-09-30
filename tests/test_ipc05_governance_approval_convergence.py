import asyncio
import inspect

import pytest

from services.action_kernel import ActionContract, ActionKernel


def test_kernel_has_no_caller_controlled_approval_argument():
    assert "approved" not in inspect.signature(ActionKernel.enqueue).parameters
    assert "approved" not in inspect.signature(ActionKernel.execute).parameters


@pytest.mark.asyncio
async def test_governed_action_requires_decision_before_approval():
    k = ActionKernel(allow_in_memory=True)
    k.register(ActionContract("danger", risk_level="high", requires_approval=True, handler=lambda p, i: {"object_id": "x"}))
    out = await k.enqueue(workspace_id="ws", action_type="danger", risk_level="high", idempotency_key="g1")
    assert out["status"] == "governance_required"
    assert out["accepted"] is False


@pytest.mark.asyncio
async def test_approval_evidence_is_required_before_worker_execution():
    k = ActionKernel(allow_in_memory=True)
    k.register(ActionContract("danger", risk_level="high", requires_approval=True, handler=lambda p, i: {"object_id": "x"}))
    out = await k.enqueue(workspace_id="ws", action_type="danger", decision_id="decision-1", risk_level="high", idempotency_key="g2")
    assert out["status"] == "approval_required"
    row = k._memory_intents[out["intent_id"]]
    forged = dict(row)
    forged["approved"] = True
    result = await k.process_outbox_row(forged)
    assert result["status"] == "approval_required"


@pytest.mark.asyncio
async def test_authoritative_approval_releases_worker():
    calls = []
    k = ActionKernel(allow_in_memory=True)
    k.register(ActionContract("danger", risk_level="high", requires_approval=True, handler=lambda p, i: calls.append(1) or {"object_id": "x"}))
    out = await k.enqueue(workspace_id="ws", action_type="danger", decision_id="decision-2", risk_level="high", idempotency_key="g3")
    await k.approve(workspace_id="ws", intent_id=out["intent_id"], approver_id="u1", approver_role="operator", approval_source="test")
    result = await k.process_outbox_row(k._memory_intents[out["intent_id"]])
    assert result["status"] == "succeeded"
    assert calls == [1]
