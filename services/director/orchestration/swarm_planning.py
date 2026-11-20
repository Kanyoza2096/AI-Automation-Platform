"""Bounded, deterministic planning for Director-managed specialist swarms.

This module only validates a Director-proposed roster. It does not spawn agents,
invoke engines, grant tools, or perform external actions. Runtime persistence and
execution remain owned by the Director mission runtime.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence


class _Adapter(Protocol):
    capabilities: frozenset[str]


class _Registry(Protocol):
    def get(self, engine_id: str) -> _Adapter: ...


@dataclass(frozen=True)
class SwarmLimits:
    max_agents: int = 8
    max_parallel_agents: int = 4
    max_agents_per_task: int = 3
    max_total_estimated_tokens: int = 12000

    def __post_init__(self) -> None:
        for name in (
            "max_agents",
            "max_parallel_agents",
            "max_agents_per_task",
            "max_total_estimated_tokens",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name}_must_be_positive_integer")
        if self.max_parallel_agents > self.max_agents:
            raise ValueError("max_parallel_agents_cannot_exceed_max_agents")
        if self.max_agents_per_task > self.max_agents:
            raise ValueError("max_agents_per_task_cannot_exceed_max_agents")


@dataclass(frozen=True)
class SwarmCandidate:
    """A proposed worker assignment; never contains executable tool handles."""

    agent_id: str
    task_id: str
    engine_id: str
    capability: str
    role: str
    priority: int = 0
    estimated_tokens: int = 1500

    def __post_init__(self) -> None:
        for name in ("agent_id", "task_id", "engine_id", "capability", "role"):
            value = str(getattr(self, name) or "").strip()
            if not value or len(value) > 128:
                raise ValueError(f"swarm_candidate_{name}_invalid")
            object.__setattr__(self, name, value)
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ValueError("swarm_candidate_priority_must_be_integer")
        if (
            isinstance(self.estimated_tokens, bool)
            or not isinstance(self.estimated_tokens, int)
            or self.estimated_tokens < 0
        ):
            raise ValueError("swarm_candidate_estimated_tokens_invalid")


@dataclass(frozen=True)
class SwarmPlan:
    complexity_score: float
    target_agent_count: int
    selected: tuple[SwarmCandidate, ...]
    deferred: tuple[SwarmCandidate, ...]
    estimated_tokens: int
    max_parallel_agents: int


def plan_swarm(
    *,
    complexity_score: float,
    task_ids: Iterable[str],
    candidates: Sequence[SwarmCandidate],
    registry: _Registry,
    limits: SwarmLimits | None = None,
) -> SwarmPlan:
    """Validate and bound a Director-proposed roster against trusted adapters.

    Complexity is normalized to [0, 1]. It determines a target team size, while
    available candidates, task scope, adapter capabilities, per-task diversity,
    concurrency, and token ceilings determine the actual selected roster.
    """
    limits = limits or SwarmLimits()
    if (
        isinstance(complexity_score, bool)
        or not isinstance(complexity_score, (int, float))
        or not math.isfinite(float(complexity_score))
        or not 0.0 <= float(complexity_score) <= 1.0
    ):
        raise ValueError("swarm_complexity_score_out_of_range")

    scoped_tasks = {str(task_id).strip() for task_id in task_ids if str(task_id).strip()}
    if not scoped_tasks:
        raise ValueError("swarm_requires_existing_mission_tasks")
    if len(candidates) > 64:
        raise ValueError("swarm_candidate_limit_exceeded")

    target = max(1, min(limits.max_agents, math.ceil(float(complexity_score) * limits.max_agents)))
    seen_agent_ids: set[str] = set()
    seen_assignments: set[tuple[str, str, str]] = set()
    validated: list[SwarmCandidate] = []

    for candidate in candidates:
        if not isinstance(candidate, SwarmCandidate):
            raise TypeError("swarm_candidates_must_be_typed_assignments")
        if candidate.agent_id in seen_agent_ids:
            raise ValueError("duplicate_swarm_agent_id")
        seen_agent_ids.add(candidate.agent_id)
        if candidate.task_id not in scoped_tasks:
            raise ValueError("swarm_candidate_task_out_of_mission_scope")
        assignment_key = (candidate.task_id, candidate.engine_id, candidate.capability)
        if assignment_key in seen_assignments:
            raise ValueError("duplicate_swarm_specialist_assignment")
        seen_assignments.add(assignment_key)
        adapter = registry.get(candidate.engine_id)
        if candidate.capability not in adapter.capabilities:
            raise ValueError("swarm_candidate_capability_not_registered")
        validated.append(candidate)

    # Stable deterministic ordering makes replay and audit records reproducible.
    validated.sort(key=lambda item: (-item.priority, item.task_id, item.engine_id, item.agent_id))
    per_task: dict[str, int] = {}
    selected: list[SwarmCandidate] = []
    deferred: list[SwarmCandidate] = []
    tokens = 0

    for candidate in validated:
        task_count = per_task.get(candidate.task_id, 0)
        fits = (
            len(selected) < target
            and len(selected) < limits.max_parallel_agents
            and task_count < limits.max_agents_per_task
            and tokens + candidate.estimated_tokens <= limits.max_total_estimated_tokens
        )
        if fits:
            selected.append(candidate)
            per_task[candidate.task_id] = task_count + 1
            tokens += candidate.estimated_tokens
        else:
            deferred.append(candidate)

    return SwarmPlan(
        complexity_score=float(complexity_score),
        target_agent_count=target,
        selected=tuple(selected),
        deferred=tuple(deferred),
        estimated_tokens=tokens,
        max_parallel_agents=limits.max_parallel_agents,
    )
