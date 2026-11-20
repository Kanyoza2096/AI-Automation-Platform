from types import SimpleNamespace

import pytest

from services.director.orchestration.director_swarm import (
    SwarmAssessmentError,
    SwarmAssessmentUnavailable,
    assess_swarm_roster,
)


class FakeGateway:
    def __init__(self, data=None, ok=True):
        self.data = data
        self.ok = ok
        self.kwargs = None
        self.prompt = None

    async def complete_async_structured(self, prompt, **kwargs):
        self.prompt = prompt
        self.kwargs = kwargs
        return SimpleNamespace(
            ok=self.ok,
            data=self.data,
            provider="test-provider",
            model="test-model",
            request_id="request-123",
        )


def _task():
    return {
        "task_id": "task-1",
        "description": "Assess the admitted market research task.",
        "capability": "research.analyze",
        "sector": "growth",
        "status": "approval_required",
        "success_criteria": ["Evidence-backed findings"],
        "params": {"secret": "must-not-enter-prompt"},
    }


def _catalogue():
    return [{
        "engine_id": "content_intelligence",
        "capabilities": ["research.analyze"],
        "authority": "none",
    }]


def _response(**overrides):
    data = {
        "complexity_score": 0.65,
        "rationale": "Independent evidence collection and synthesis are useful.",
        "spawn_recommended": True,
        "assignments": [{
            "task_id": "task-1",
            "engine_id": "content_intelligence",
            "capability": "research.analyze",
            "role": "Evidence researcher",
            "priority": 10,
            "estimated_tokens": 1200,
        }],
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_director_assesses_complexity_and_selects_only_registered_native_capabilities():
    gateway = FakeGateway(_response())
    result = await assess_swarm_roster(
        gateway=gateway,
        workspace_id="workspace-1",
        mission_id="mission-1",
        idempotency_prefix="wave-0001",
        tasks=[_task()],
        adapter_catalogue=_catalogue(),
    )

    assert result.complexity_score == 0.65
    assert result.spawn_recommended is True
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.engine_id == "content_intelligence"
    assert candidate.capability == "research.analyze"
    assert candidate.task_id == "task-1"
    assert candidate.agent_id.startswith("swarm-")
    assert gateway.kwargs["workspace_id"] == "workspace-1"
    assert gateway.kwargs["caller"] == "director"
    assert "must-not-enter-prompt" not in gateway.prompt
    assert result.request_id == "request-123"


@pytest.mark.asyncio
async def test_director_can_decide_no_followup_specialists_are_needed():
    gateway = FakeGateway(_response(
        complexity_score=0.25,
        spawn_recommended=False,
        assignments=[],
    ))
    result = await assess_swarm_roster(
        gateway=gateway,
        workspace_id="workspace-1",
        mission_id="mission-1",
        idempotency_prefix="wave-0002",
        tasks=[_task()],
        adapter_catalogue=_catalogue(),
        existing_agents={
            "agent-1": {
                "task_id": "task-1",
                "engine_id": "content_intelligence",
                "capability": "research.analyze",
                "status": "completed",
                "contribution": {
                    "status": "completed",
                    "summary": "Evidence collection is complete.",
                    "findings": ["The evidence gap is closed."],
                },
            }
        },
    )
    assert result.spawn_recommended is False
    assert result.candidates == ()
    assert "The evidence gap is closed." in gateway.prompt


@pytest.mark.asyncio
async def test_director_cannot_invent_unregistered_engine_capability_or_task():
    gateway = FakeGateway(_response(assignments=[{
        "task_id": "task-1",
        "engine_id": "unknown_engine",
        "capability": "admin.execute",
        "role": "Untrusted executor",
        "priority": 1,
        "estimated_tokens": 1000,
    }]))
    with pytest.raises(SwarmAssessmentError, match="outside_registered_scope"):
        await assess_swarm_roster(
            gateway=gateway,
            workspace_id="workspace-1",
            mission_id="mission-1",
            idempotency_prefix="wave-0003",
            tasks=[_task()],
            adapter_catalogue=_catalogue(),
        )


@pytest.mark.asyncio
async def test_director_assessment_fails_closed_when_gateway_is_unavailable():
    gateway = FakeGateway(ok=False)
    with pytest.raises(SwarmAssessmentUnavailable):
        await assess_swarm_roster(
            gateway=gateway,
            workspace_id="workspace-1",
            mission_id="mission-1",
            idempotency_prefix="wave-0004",
            tasks=[_task()],
            adapter_catalogue=_catalogue(),
        )


@pytest.mark.asyncio
async def test_director_rejects_roster_when_no_spawn_is_recommended_but_assignments_exist():
    gateway = FakeGateway(_response(spawn_recommended=False))
    with pytest.raises(SwarmAssessmentError, match="no_spawn_must_have_empty_roster"):
        await assess_swarm_roster(
            gateway=gateway,
            workspace_id="workspace-1",
            mission_id="mission-1",
            idempotency_prefix="wave-0005",
            tasks=[_task()],
            adapter_catalogue=_catalogue(),
        )


@pytest.mark.asyncio
async def test_director_does_not_repeat_a_completed_specialist_assignment_in_followup_wave():
    gateway = FakeGateway(_response())
    with pytest.raises(SwarmAssessmentError, match="assignment_already_covered"):
        await assess_swarm_roster(
            gateway=gateway,
            workspace_id="workspace-1",
            mission_id="mission-1",
            idempotency_prefix="wave-0006",
            tasks=[_task()],
            adapter_catalogue=_catalogue(),
            existing_agents={
                "agent-previous": {
                    "task_id": "task-1",
                    "engine_id": "content_intelligence",
                    "capability": "research.analyze",
                    "status": "completed",
                }
            },
        )
