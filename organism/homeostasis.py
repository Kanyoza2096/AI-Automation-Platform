"""Bounded homeostatic control for the canonical organism state.

Homeostasis proposes corrective responses; it never executes them or changes
security/authority. It is intentionally policy-like so existing metabolism,
Guardian and execution services remain authoritative for their domains.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from core.organism_state import OrganismState


class HomeostaticAction(str, Enum):
    REDUCE_LOAD = "reduce_load"
    DEFER_NON_CRITICAL = "defer_non_critical"
    REQUEST_REVIEW = "request_review"
    STABILIZE_DEPENDENCIES = "stabilize_dependencies"
    RESTORE_RESERVE = "restore_reserve"
    CONTINUE = "continue"


@dataclass(frozen=True)
class HomeostaticProposal:
    workspace_id: str
    action: HomeostaticAction
    urgency: float
    reason: str
    reversible: bool = True
    execution_required: bool = False


def assess_homeostasis(state: OrganismState) -> Tuple[HomeostaticProposal, ...]:
    """Return bounded corrective proposals ordered by urgency."""
    proposals: list[HomeostaticProposal] = []
    def add(action: HomeostaticAction, urgency: float, reason: str, execution: bool = False):
        proposals.append(HomeostaticProposal(state.workspace_id, action, max(0.0, min(1.0, urgency)), reason, True, execution))

    if not state.topology_healthy:
        add(HomeostaticAction.STABILIZE_DEPENDENCIES, 1.0, "topology is unhealthy", True)
    if state.security_posture < 0.35:
        add(HomeostaticAction.REQUEST_REVIEW, 1.0, "security posture is below autonomous threshold")
    if state.resource_reserve < 0.25:
        add(HomeostaticAction.RESTORE_RESERVE, 0.85, "resource reserve is low", True)
    if state.operational_load > 0.85:
        add(HomeostaticAction.REDUCE_LOAD, 0.8, "operational load is high", True)
    if state.cognitive_load > 0.85:
        add(HomeostaticAction.DEFER_NON_CRITICAL, 0.75, "cognitive load is high", True)
    if not proposals:
        add(HomeostaticAction.CONTINUE, 0.0, "organism is within homeostatic thresholds")
    return tuple(sorted(proposals, key=lambda p: p.urgency, reverse=True))
