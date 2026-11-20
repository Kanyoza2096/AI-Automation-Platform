import pytest

from services.director.orchestration.swarm_planning import (
    SwarmCandidate,
    SwarmLimits,
    plan_swarm,
)


class Adapter:
    def __init__(self, *capabilities):
        self.capabilities = frozenset(capabilities)


class Registry:
    def __init__(self):
        self.adapters = {
            "research": Adapter("research.analyze"),
            "condition": Adapter("brand.health"),
            "meta": Adapter("platform.health"),
        }

    def get(self, engine_id):
        return self.adapters[engine_id]


def candidate(agent_id, task_id, engine_id, capability, priority=0, tokens=1000):
    return SwarmCandidate(
        agent_id=agent_id,
        task_id=task_id,
        engine_id=engine_id,
        capability=capability,
        role=f"{engine_id} specialist",
        priority=priority,
        estimated_tokens=tokens,
    )


def test_complexity_scales_target_agent_count_dynamically():
    registry = Registry()
    candidates = [
        candidate("a1", "t1", "research", "research.analyze"),
        candidate("a2", "t1", "condition", "brand.health"),
        candidate("a3", "t2", "meta", "platform.health"),
        candidate("a4", "t2", "research", "research.analyze"),
    ]

    simple = plan_swarm(
        complexity_score=0.2, task_ids=["t1", "t2"],
        candidates=candidates, registry=registry,
    )
    complex_plan = plan_swarm(
        complexity_score=0.9, task_ids=["t1", "t2"],
        candidates=candidates, registry=registry,
        limits=SwarmLimits(max_agents=8, max_parallel_agents=8),
    )

    assert simple.target_agent_count == 2
    assert len(simple.selected) == 2
    assert complex_plan.target_agent_count == 8
    assert len(complex_plan.selected) == 4


def test_untrusted_or_out_of_scope_engine_capabilities_are_rejected():
    with pytest.raises(ValueError, match="task_out_of_mission_scope"):
        plan_swarm(
            complexity_score=0.5,
            task_ids=["mission-task"],
            candidates=[candidate("a1", "other-task", "research", "research.analyze")],
            registry=Registry(),
        )

    with pytest.raises(ValueError, match="capability_not_registered"):
        plan_swarm(
            complexity_score=0.5,
            task_ids=["mission-task"],
            candidates=[candidate("a1", "mission-task", "research", "admin.execute")],
            registry=Registry(),
        )


def test_roster_is_bounded_by_concurrency_per_task_and_token_budget():
    plan = plan_swarm(
        complexity_score=1.0,
        task_ids=["t1", "t2"],
        candidates=[
            candidate("a1", "t1", "research", "research.analyze", priority=10, tokens=4000),
            candidate("a2", "t1", "condition", "brand.health", priority=9, tokens=4000),
            candidate("a3", "t1", "meta", "platform.health", priority=8, tokens=4000),
            candidate("a4", "t2", "research", "research.analyze", priority=7, tokens=4000),
        ],
        registry=Registry(),
        limits=SwarmLimits(
            max_agents=8,
            max_parallel_agents=3,
            max_agents_per_task=2,
            max_total_estimated_tokens=9000,
        ),
    )

    assert [item.agent_id for item in plan.selected] == ["a1", "a2"]
    assert [item.agent_id for item in plan.deferred] == ["a4", "a3"]
    assert plan.estimated_tokens == 8000


def test_duplicate_agent_ids_and_assignments_are_rejected():
    with pytest.raises(ValueError, match="duplicate_swarm_agent_id"):
        plan_swarm(
            complexity_score=0.8, task_ids=["t1"],
            candidates=[
                candidate("same", "t1", "research", "research.analyze"),
                candidate("same", "t1", "condition", "brand.health"),
            ],
            registry=Registry(),
        )

    with pytest.raises(ValueError, match="duplicate_swarm_specialist_assignment"):
        plan_swarm(
            complexity_score=0.8, task_ids=["t1"],
            candidates=[
                candidate("a1", "t1", "research", "research.analyze"),
                candidate("a2", "t1", "research", "research.analyze"),
            ],
            registry=Registry(),
        )


@pytest.mark.parametrize("score", [-0.1, 1.1, float("nan"), float("inf"), True])
def test_invalid_complexity_score_is_rejected(score):
    with pytest.raises(ValueError, match="complexity_score_out_of_range"):
        plan_swarm(
            complexity_score=score, task_ids=["t1"],
            candidates=[], registry=Registry(),
        )


def test_no_mission_tasks_means_no_swarm():
    with pytest.raises(ValueError, match="existing_mission_tasks"):
        plan_swarm(
            complexity_score=0.5, task_ids=[],
            candidates=[], registry=Registry(),
        )
