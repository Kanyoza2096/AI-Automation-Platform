"""Regression tests for single-shot Director task execution."""
import asyncio
from services.director import Director
from services.director.models import Status


def make_task(director, capability, description="idempotency regression"):
    return director._create_task(
        sector=capability.sector, desc=description, cap_name=capability.name,
        reason="test", params={"key": "value"},
    )


def test_completed_task_is_not_executed_again():
    director = Director(supabase_client=None)
    calls = []
    capability = next(iter(director.capabilities.values()))
    capability.handler = lambda params: calls.append(params) or {"ok": True}
    task = make_task(director, capability)
    task.status = Status.COMPLETED
    result = asyncio.run(director._execute_async(task.id))
    assert result is task and result.status == Status.COMPLETED
    assert calls == []


def test_approval_required_task_is_not_replayed_without_explicit_reset():
    director = Director(supabase_client=None)
    calls = []
    capability = next(iter(director.capabilities.values()))
    capability.handler = lambda params: calls.append(params) or {"ok": True}
    task = make_task(director, capability)
    task.status = Status.APPROVAL
    result = asyncio.run(director._execute_async(task.id))
    assert result is task and result.status == Status.APPROVAL
    assert calls == []


def test_concurrent_duplicate_execution_invokes_handler_once():
    async def scenario():
        director = Director(supabase_client=None)
        calls, entered, release = [], asyncio.Event(), asyncio.Event()
        capability = next(iter(director.capabilities.values()))
        async def handler(params):
            calls.append(params); entered.set(); await release.wait(); return {"ok": True}
        capability.handler = handler
        task = make_task(director, capability, "concurrent idempotency regression")
        first = asyncio.create_task(director._execute_async(task.id))
        await entered.wait()
        second = await director._execute_async(task.id)
        assert second is task and task.status == Status.EXECUTING
        release.set()
        assert (await first).status == Status.COMPLETED
        assert len(calls) == 1
    asyncio.run(scenario())


def test_execution_is_not_started_when_in_flight_state_cannot_be_saved():
    director = Director(supabase_client=None)
    calls = []
    capability = next(iter(director.capabilities.values()))
    capability.handler = lambda params: calls.append(params) or {"ok": True}
    task = make_task(director, capability, "persistence gate")
    async def fail_save(_task): return False
    director.store.save_task = fail_save
    result = asyncio.run(director._execute_async(task.id))
    assert result is task and result.status == Status.FLAGGED
    assert result.error == "execution_not_started:in_flight_state_persistence_failed"
    assert calls == []


def test_governed_handoff_is_not_called_when_in_flight_state_cannot_be_saved():
    from services.director.models import Proposal, ProposalStatus, Risk
    director = Director(supabase_client=None)
    capability = next(iter(director.capabilities.values()))
    proposal = Proposal(
        id="persist-before-handoff", workspace_id="ws-test", sector=capability.sector,
        title="persistence gate", rationale="test", capability=capability.name,
        params={}, target_param_value=None, expected_benefit="test", estimated_cost="0",
        risk=Risk.LOW, confidence=0.9, signal_ids=[], status=ProposalStatus.PENDING,
    )
    director.proposals[proposal.id] = proposal
    task = director.approve_proposal(proposal.id, actor="test-actor")
    calls = []
    async def handoff(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "status": "succeeded", "execution_id": "e1", "intent_id": "i1"}
    capability.governed_handoff = handoff
    async def fail_save(_task): return False
    director.store.save_task = fail_save
    result = asyncio.run(director._execute_async(task.id))
    assert result is task and result.status == Status.FLAGGED
    assert calls == []


def test_approved_proposal_uses_governed_handoff_and_never_legacy_handler():
    from services.director.models import Proposal, ProposalStatus, Risk

    director = Director(supabase_client=None)
    capability = director.capabilities["optimize_content_schedule"]
    legacy_calls, handoff_calls = [], []
    capability.handler = lambda params: legacy_calls.append(params) or {"ok": True}
    async def handoff(**kwargs):
        handoff_calls.append(kwargs)
        return {"ok": True, "status": "succeeded", "execution_id": "exec-123", "intent_id": "intent-123"}
    capability.governed_handoff = handoff
    proposal = Proposal(
        id="proposal-e2e-governance", workspace_id="ws-test", sector=capability.sector,
        title="Review content schedule", rationale="Verified engine recommendation",
        capability=capability.name, params={"brand_id": "brand-7"},
        target_param_value=None, expected_benefit="Close schedule gap", estimated_cost="0",
        risk=Risk.LOW, confidence=0.9, signal_ids=[], status=ProposalStatus.PENDING,
    )
    director.proposals[proposal.id] = proposal

    task = director.approve_proposal(proposal.id, actor="reviewer-1")
    result = asyncio.run(director._execute_async(task.id))

    assert result.status == Status.COMPLETED
    assert proposal.status == ProposalStatus.EXECUTED
    assert len(handoff_calls) == 1
    assert legacy_calls == []
    assert handoff_calls[0]["actor"] == "reviewer-1"
