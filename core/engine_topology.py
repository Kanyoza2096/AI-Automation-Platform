"""Canonical runtime engine topology.

This module is the single dependency declaration consumed by supervisors.
It describes runtime dependency direction as: ``engine -> dependencies``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Tuple

CANONICAL_ENGINE_DEPENDENCIES: Dict[str, Tuple[str, ...]] = {
    # Infrastructure providers are first-class topology nodes so every dependency
    # edge is declared rather than being an implicit/external graph escape hatch.
    "redis": (),
    "supabase": (),
    "gemini": (),
    "github": (),
    "event_bus": (),
    "task_queue": ("redis", "supabase"),
    "job_service": ("supabase", "event_bus"),
    "job_stats_service": ("supabase",),
    "plugin_registry": ("event_bus",),
    "render_queue": ("redis",),
    "event_recorder": ("event_bus", "supabase"),
    "ai_orchestrator": ("supabase", "gemini"),
    "prompt_builder": (),
    "content_engine": ("ai_orchestrator", "event_bus", "supabase", "prompt_builder"),
    "card_renderer": ("render_queue", "content_engine"),
    "image_engine": ("render_queue", "content_engine"),
    "condition_engine": ("event_bus", "supabase", "content_engine", "engagement_tracker"),
    "director": ("event_bus", "supabase"),
    "meta_engine": ("event_bus", "supabase"),
    "brand_scheduler": ("job_service", "content_engine", "supabase"),
    "email_service": ("event_bus",),
    "engagement_tracker": ("supabase",),
    "guardian": ("supabase", "github"),
    "guardian_scheduler": ("guardian",),
    "health_monitor": ("event_bus",),
    "enterprise_emitters": ("event_bus",),
    "scheduler": ("job_service", "content_engine"),
    "autonomous_content": ("ai_orchestrator", "content_engine", "event_bus", "supabase"),
    "autonomous_loop": ("meta_engine", "director", "condition_engine", "autonomous_content", "event_bus"),
}

# Stable, platform-owned capability declarations. Runtime readiness is always
# derived from the owning service observation and its dependency graph; these
# identifiers describe what a service provides, not whether it is currently ready.
CANONICAL_ENGINE_CAPABILITIES: Dict[str, Tuple[str, ...]] = {
    "event_bus": ("event_transport", "event_replay"),
    "task_queue": ("async_task_execution",),
    "job_service": ("job_lifecycle_management",),
    "job_stats_service": ("job_metrics",),
    "ai_orchestrator": ("ai_task_orchestration",),
    "content_engine": ("content_generation",),
    "condition_engine": ("condition_analysis", "decision_recommendation"),
    "director": ("strategic_planning", "platform_coordination"),
    "meta_engine": ("service_supervision", "health_assessment"),
    "health_monitor": ("health_observation", "platform_state_publication"),
    "brand_scheduler": ("brand_schedule_execution",),
    "autonomous_loop": ("autonomous_coordination",),
}


def dependency_pairs() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return immutable dependency declarations for consumers."""
    return tuple(CANONICAL_ENGINE_DEPENDENCIES.items())


@dataclass(frozen=True)
class TopologyDrift:
    """Deterministic comparison between declared and observed runtime topology."""

    missing_runtime_nodes: Tuple[str, ...] = ()
    undeclared_runtime_nodes: Tuple[str, ...] = ()
    invalid_dependencies: Tuple[Tuple[str, str], ...] = ()
    cycles: Tuple[Tuple[str, ...], ...] = ()

    @property
    def healthy(self) -> bool:
        return not (
            self.missing_runtime_nodes
            or self.undeclared_runtime_nodes
            or self.invalid_dependencies
            or self.cycles
        )


def validate_topology(
    runtime_nodes: Iterable[str],
    topology: Mapping[str, Tuple[str, ...]] = CANONICAL_ENGINE_DEPENDENCIES,
) -> TopologyDrift:
    """Validate runtime registration against the canonical dependency graph."""
    declared = set(topology)
    runtime = {str(name) for name in runtime_nodes if name}
    missing = tuple(sorted(declared - runtime))
    undeclared = tuple(sorted(runtime - declared))
    invalid = tuple(sorted(
        (engine, dependency)
        for engine, dependencies in topology.items()
        for dependency in dependencies
        if dependency not in declared
    ))

    # Kahn-style cycle detection. Dependencies point from consumer -> provider.
    adjacency = {name: set(dependencies) for name, dependencies in topology.items()}
    indegree = {name: 0 for name in declared}
    reverse = {name: set() for name in declared}
    for consumer, dependencies in adjacency.items():
        for dependency in dependencies:
            if dependency in declared:
                indegree[consumer] += 1
                reverse[dependency].add(consumer)
    queue = sorted(name for name, degree in indegree.items() if degree == 0)
    visited = []
    while queue:
        node = queue.pop(0)
        visited.append(node)
        for dependent in sorted(reverse[node]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
    cycles = () if len(visited) == len(declared) else (tuple(sorted(declared - set(visited))),)

    return TopologyDrift(
        missing_runtime_nodes=missing,
        undeclared_runtime_nodes=undeclared,
        invalid_dependencies=invalid,
        cycles=cycles,
    )
