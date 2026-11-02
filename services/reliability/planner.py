"""Repair Planner — typed remediation plans from diagnosis."""

from __future__ import annotations

import logging
from typing import Optional

from services.reliability.models import (
    AutonomyLevel,
    Diagnosis,
    Incident,
    RemediationAction,
    RemediationPlan,
)
from services.reliability.memory import IncidentMemory
from services.reliability.policies import AutonomyPolicy

logger = logging.getLogger("kanyoza.reliability.planner")


# cause → default action mapping (playbook catalog)
CAUSE_PLAYBOOK = {
    "health_aggregation_inconsistency": RemediationAction.RECONCILE_HEALTH_SCOPE,
    "stale_idle_engines_inflating_health": RemediationAction.CLEAR_STALE_STATE,
    "engine_degradation": RemediationAction.RESTART_WORKER,
    "offline_engines": RemediationAction.RESTART_WORKER,
    # Generic connection refresh has no executor yet. Escalate rather than
    # repeatedly scheduling a known-unimplemented remediation.
    "degraded_engines": RemediationAction.ESCALATE_HUMAN,
    "service_health_degraded": RemediationAction.ESCALATE_HUMAN,
    "nerve_event_scope_mismatch": RemediationAction.RECONCILE_HEALTH_SCOPE,
    "activity_stagnation": RemediationAction.ESCALATE_HUMAN,
    "connector_auth_failure": RemediationAction.ESCALATE_HUMAN,
    "unknown_degradation": RemediationAction.OBSERVE_ONLY,
}


class RepairPlanner:
    def __init__(self, policy: AutonomyPolicy, memory: Optional[IncidentMemory] = None):
        self._policy = policy
        self._memory = memory or IncidentMemory()

    def plan(self, incident: Incident, diagnosis: Diagnosis) -> RemediationPlan:
        # These generic diagnoses currently have no connection-refresh adapter.
        # Do not let historical memory resurrect the unsupported action; require
        # human review until a component-specific, tested executor exists.
        if diagnosis.probable_cause in {"degraded_engines", "service_health_degraded"}:
            action = RemediationAction.ESCALATE_HUMAN
        else:
            # Memory can override the default playbook for supported actions.
            remembered = self._memory.best_action_for(diagnosis.probable_cause)
            if remembered:
                try:
                    action = RemediationAction(remembered)
                except ValueError:
                    action = CAUSE_PLAYBOOK.get(diagnosis.probable_cause, RemediationAction.OBSERVE_ONLY)
            else:
                action = CAUSE_PLAYBOOK.get(diagnosis.probable_cause, RemediationAction.OBSERVE_ONLY)

        risk = "low"
        if action in (RemediationAction.DISABLE_CONNECTOR, RemediationAction.ROLLBACK_RELEASE):
            risk = "high"
        elif action in (RemediationAction.RESTART_WORKER, RemediationAction.CLEAR_STALE_STATE):
            risk = "medium"
        if action == RemediationAction.ESCALATE_HUMAN:
            risk = "low"

        requires_human = self._policy.requires_human(action)
        autonomy_required = AutonomyLevel.REVERSIBLE
        if action == RemediationAction.PROPOSE_PATCH:
            autonomy_required = AutonomyLevel.PATCH_PROPOSE
            requires_human = True
        elif action in (RemediationAction.ROLLBACK_RELEASE, RemediationAction.DISABLE_CONNECTOR):
            autonomy_required = AutonomyLevel.PLAYBOOK
            requires_human = True
        elif action == RemediationAction.ESCALATE_HUMAN:
            autonomy_required = AutonomyLevel.RECOMMEND
            requires_human = True
        elif action == RemediationAction.OBSERVE_ONLY:
            autonomy_required = AutonomyLevel.OBSERVE

        component = incident.component or (diagnosis.related_components[0] if diagnosis.related_components else "system")

        return RemediationPlan(
            incident_id=incident.id,
            action=action,
            risk=risk,
            confidence=diagnosis.confidence,
            reason=(
                f"{diagnosis.probable_cause} (confidence={diagnosis.confidence:.0%}): "
                f"{'; '.join(diagnosis.evidence_summary[:3])}"
            ),
            params={
                "component": component,
                "cause": diagnosis.probable_cause,
                "similar": diagnosis.similar_incident_ids,
            },
            requires_human=requires_human,
            autonomy_required=autonomy_required,
        )
