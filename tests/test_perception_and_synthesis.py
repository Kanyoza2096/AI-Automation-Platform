from datetime import datetime, timedelta, timezone

import pytest

from core.organism_state import OperatingMode
from organism.goals import Goal, GoalPriority, GoalState
from organism.perception import Observation, ObservationKind, PerceptionBuffer
from organism.state_synthesis import synthesize_state

NOW = datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc)


def test_observation_is_tenant_scoped_and_serializable():
    obs = Observation("ws", ObservationKind.HEALTH, "meta", {"health": 0.9})
    assert obs.to_dict()["workspace_id"] == "ws"
    assert obs.to_dict()["kind"] == "health"


def test_perception_buffer_is_bounded_per_workspace():
    buffer = PerceptionBuffer(max_observations_per_workspace=2)
    for i in range(3):
        buffer.ingest(Observation("ws", ObservationKind.EXTERNAL, "test", i))
    assert [o.value for o in buffer.recent("ws")] == [1, 2]
    assert buffer.recent("other") == ()


def test_synthesis_combines_existing_signals_without_granting_authority():
    observations = (
        Observation("ws", ObservationKind.HEALTH, "meta", {"health": 0.9, "cognitive_load": 0.2}, observed_at=NOW),
        Observation("ws", ObservationKind.SECURITY, "guardian", {"security_posture": 0.95}, observed_at=NOW),
        Observation("ws", ObservationKind.RESOURCE, "metabolism", {"resource_reserve": 0.8}, observed_at=NOW),
        Observation("ws", ObservationKind.EXECUTION, "ledger", {"operational_load": 0.4}, observed_at=NOW),
    )
    goal = Goal("ws", "publish campaign", "published", priority=GoalPriority.HIGH, authority="publish", progress=0.4, state=GoalState.ACTIVE)
    state = synthesize_state("ws", observations=observations, goals=(goal,), now=NOW)
    assert state.mode == OperatingMode.EXECUTING
    assert state.objective == "publish campaign"
    assert state.authority == "publish"
    assert state.health == 0.9
    assert state.security_posture == 0.95


def test_security_degradation_quarantines_state():
    obs = Observation("ws", ObservationKind.SECURITY, "guardian", {"security_posture": 0.2}, observed_at=NOW)
    state = synthesize_state("ws", observations=(obs,), now=NOW)
    assert state.mode == OperatingMode.QUARANTINED
    assert state.recommended_action


def test_topology_failure_is_degraded_not_hidden():
    state = synthesize_state("ws", topology_healthy=False, now=NOW)
    assert state.mode == OperatingMode.DEGRADED
    assert state.topology_healthy is False
    assert state.concern == "topology drift or dependency failure"


def test_expired_goal_does_not_become_false_success():
    goal = Goal("ws", "deadline", "done", deadline=NOW - timedelta(minutes=1), state=GoalState.ACTIVE, progress=0.4)
    state = synthesize_state("ws", goals=(goal,), now=NOW)
    assert state.goal_progress == 0.4
    assert state.risk > 0.0


def test_observation_source_trust_contributes_to_state_confidence():
    obs = Observation("ws", ObservationKind.HEALTH, "external", {"health": 0.9}, confidence=1.0, source_trust=0.2, observed_at=NOW)
    state = synthesize_state("ws", observations=(obs,), now=NOW)
    assert state.confidence.value == "low"


def test_observation_rejects_invalid_source_trust():
    with pytest.raises(ValueError, match="source_trust"):
        Observation("ws", ObservationKind.EXTERNAL, "source", 1, source_trust=1.1)
