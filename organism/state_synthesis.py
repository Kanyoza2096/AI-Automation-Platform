"""Pure synthesis of the canonical OrganismState from authoritative signals."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from core.organism_state import Confidence, OperatingMode, OrganismState
from organism.goals import Goal, GoalState
from organism.perception import Observation, ObservationKind


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def synthesize_state(
    workspace_id: str,
    *,
    observations: tuple[Observation, ...] = (),
    goals: tuple[Goal, ...] = (),
    topology_healthy: bool = True,
    source_versions: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
) -> OrganismState:
    """Derive a bounded snapshot; callers remain responsible for authority."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    obs = tuple(o for o in observations if o.workspace_id == workspace_id)

    def latest(kind: ObservationKind) -> Optional[Observation]:
        values = [o for o in obs if o.kind == kind]
        return max(values, key=lambda o: o.observed_at) if values else None

    health_obs = latest(ObservationKind.HEALTH)
    resource_obs = latest(ObservationKind.RESOURCE)
    security_obs = latest(ObservationKind.SECURITY)
    execution_obs = latest(ObservationKind.EXECUTION)
    learning_obs = latest(ObservationKind.LEARNING)

    health = _signal(health_obs, "health", 1.0)
    security = _signal(security_obs, "security_posture", 1.0)
    reserve = _signal(resource_obs, "resource_reserve", 1.0)
    operational = _signal(execution_obs, "operational_load", 0.0)
    learning = _signal(learning_obs, "learning_velocity", 0.0)

    active = [g for g in goals if g.workspace_id == workspace_id and g.state not in {GoalState.ACHIEVED, GoalState.CANCELLED}]
    progress = (sum(g.progress for g in active) / len(active)) if active else 0.0
    objective = active[0].title if active else None
    authority = max((g.authority for g in active), key=lambda a: {"suggest": 0, "execute": 1, "publish": 2}[a], default="suggest")
    overdue = any(g.is_overdue(current) for g in active)
    risk = _clamp((1.0 - health) * 0.45 + (1.0 - security) * 0.35 + (1.0 - reserve) * 0.20 + (0.15 if overdue else 0.0))
    confidence_values = [o.confidence * o.source_trust for o in obs]
    confidence_score = sum(confidence_values) / len(confidence_values) if confidence_values else 0.5
    confidence = Confidence.HIGH if confidence_score >= 0.8 else Confidence.MEDIUM if confidence_score >= 0.5 else Confidence.LOW

    if not topology_healthy or security < 0.35:
        mode = OperatingMode.QUARANTINED if security < 0.35 else OperatingMode.DEGRADED
    elif execution_obs is not None and operational > 0.05:
        mode = OperatingMode.EXECUTING
    elif active:
        mode = OperatingMode.PLANNING
    else:
        mode = OperatingMode.OBSERVING

    concern = None
    recommendation = None
    if not topology_healthy:
        concern, recommendation = "topology drift or dependency failure", "stabilize dependencies before autonomous execution"
    elif security < 0.35:
        concern, recommendation = "security posture is below safe operating threshold", "quarantine autonomous actions and require review"
    elif reserve < 0.25:
        concern, recommendation = "resource reserve is low", "reduce metabolic load and defer non-critical work"
    elif overdue:
        concern, recommendation = "one or more active goals are overdue", "re-plan against current constraints"

    return OrganismState(
        workspace_id=workspace_id,
        mode=mode,
        health=health,
        cognitive_load=_signal(latest(ObservationKind.HEALTH), "cognitive_load", 0.0),
        operational_load=operational,
        security_posture=security,
        resource_reserve=reserve,
        goal_progress=progress,
        confidence=confidence,
        risk=risk,
        learning_velocity=learning,
        adaptation=_signal(learning_obs, "adaptation", 0.0),
        objective=objective,
        predicted_outcome=_text(latest(ObservationKind.GOAL), "predicted_outcome"),
        concern=concern,
        recommended_action=recommendation,
        authority=authority,
        topology_healthy=topology_healthy,
        updated_at=current,
        source_versions=dict(source_versions or {}),
    )


def _signal(observation: Optional[Observation], field: str, default: float) -> float:
    if observation is None:
        return default
    value = observation.value.get(field, default) if isinstance(observation.value, Mapping) else observation.value
    try:
        return _clamp(float(value))
    except (TypeError, ValueError):
        return default


def _text(observation: Optional[Observation], field: str) -> Optional[str]:
    if observation is None or not isinstance(observation.value, Mapping):
        return None
    value = observation.value.get(field)
    return str(value) if value is not None else None
