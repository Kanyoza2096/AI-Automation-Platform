import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_9_worker_selection import WorkerCapacityRegistry
from services.v71_21_worker_registry import WorkerRegistry, WorkerKind, WorkerHealth, WorkerLifecycle
from services.v71_22_workforce_scaling import (
    WorkforceScalingCoordinator, ScalingPolicy, ScalingObservation,
    ScalingDisposition, ScalingBlock,
)


def setup():
    ids = AgentIdentityRegistry("ws-1")
    ids.register(agent_id="w1", name="w1", purpose="ops", agent_class="ai", declared_capabilities=("ops.run",))
    ids.transition(agent_id="w1", status=AgentStatus.ACTIVE)
    cap = WorkerCapacityRegistry("ws-1", ids)
    cap.register(agent_id="w1", max_concurrent_tasks=4)
    reg = WorkerRegistry("ws-1", ids, cap)
    reg.create_pool(pool_id="ops", name="Operations", worker_kind=WorkerKind.AI,
                    capability_profile=("ops.run",), min_instances=1, desired_instances=2, max_instances=5)
    reg.register(worker_id="w1", pool_id="ops", name="w1", worker_kind=WorkerKind.AI,
                 declared_capabilities=("ops.run",), health=WorkerHealth.HEALTHY,
                 max_concurrent_tasks=4)
    reg.transition(worker_id="w1", lifecycle=WorkerLifecycle.READY)
    reg.transition(worker_id="w1", lifecycle=WorkerLifecycle.ACTIVE)
    return reg, WorkforceScalingCoordinator("ws-1", reg)


def policy(c, **kw):
    data = {"min_instances": 1, "max_instances": 5, "max_step": 2}
    data.update(kw)
    return c.register_policy(ScalingPolicy(policy_id="p1", workspace_id="ws-1", **data))


def test_scale_up_is_advisory_and_bounded():
    reg, c = setup(); policy(c)
    c.observe(ScalingObservation("o1", "ws-1", "ops", queue_depth=8, active_tasks=4, capacity_slots=4))
    d = c.decide(decision_id="d1", policy_id="p1", observation_id="o1")
    assert d.disposition == ScalingDisposition.SCALE_UP
    assert d.current_instances == 1 and d.requested_instances == 3
    assert d.advisory_only and not d.provisioning_authority_granted and not d.execution_requested
    assert reg.pool_counts("ops")[WorkerLifecycle.ACTIVE.value] == 1


def test_scale_down_respects_minimum():
    reg, c = setup(); policy(c)
    ids = reg.identity_registry
    ids.register(agent_id="w2", name="w2", purpose="ops", agent_class="ai", declared_capabilities=("ops.run",))
    ids.transition(agent_id="w2", status=AgentStatus.ACTIVE)
    reg.capacity_registry.register(agent_id="w2", max_concurrent_tasks=4)
    reg.register(worker_id="w2", pool_id="ops", name="w2", worker_kind=WorkerKind.AI,
                 declared_capabilities=("ops.run",), health=WorkerHealth.HEALTHY, max_concurrent_tasks=4)
    reg.transition(worker_id="w2", lifecycle=WorkerLifecycle.READY)
    reg.transition(worker_id="w2", lifecycle=WorkerLifecycle.ACTIVE)
    c.observe(ScalingObservation("o2", "ws-1", "ops", queue_depth=0, active_tasks=0, capacity_slots=8))
    d = c.decide(decision_id="d2", policy_id="p1", observation_id="o2")
    assert d.disposition == ScalingDisposition.SCALE_DOWN
    assert d.requested_instances == 1


def test_safety_signal_holds_scaling():
    _, c = setup(); policy(c)
    c.observe(ScalingObservation("o3", "ws-1", "ops", queue_depth=20, active_tasks=4, capacity_slots=4, failure_rate=.9))
    d = c.decide(decision_id="d3", policy_id="p1", observation_id="o3")
    assert d.disposition == ScalingDisposition.HOLD
    assert "failure_rate_above_safety_limit" in d.reason_codes


def test_emergency_stop_beats_capacity_pressure():
    _, c = setup(); policy(c)
    c.observe(ScalingObservation("o4", "ws-1", "ops", queue_depth=20, active_tasks=4, capacity_slots=4, emergency_stop=True))
    d = c.decide(decision_id="d4", policy_id="p1", observation_id="o4")
    assert d.disposition == ScalingDisposition.HOLD


def test_cooldown_holds():
    _, c = setup(); policy(c, cooldown_observations=3)
    c.observe(ScalingObservation("o5", "ws-1", "ops", queue_depth=20, active_tasks=4, capacity_slots=4, recent_scale_changes=1))
    d = c.decide(decision_id="d5", policy_id="p1", observation_id="o5")
    assert d.disposition == ScalingDisposition.HOLD
    assert "cooldown_active" in d.reason_codes


def test_policy_bounds_are_enforced():
    _, c = setup()
    with pytest.raises(ValueError, match=ScalingBlock.INVALID_POLICY.value):
        policy(c, min_instances=4, max_instances=2)


def test_cross_workspace_is_blocked():
    reg, c = setup()
    with pytest.raises(ValueError, match=ScalingBlock.TENANT_MISMATCH.value):
        c.observe(ScalingObservation("o6", "ws-2", "ops", queue_depth=0, active_tasks=0, capacity_slots=1))


def test_tamper_detection_is_fail_closed():
    _, c = setup(); policy(c)
    c.observe(ScalingObservation("o7", "ws-1", "ops", queue_depth=2, active_tasks=1, capacity_slots=4))
    d = c.decide(decision_id="d7", policy_id="p1", observation_id="o7")
    object.__setattr__(d, "requested_instances", 99)
    with pytest.raises(ValueError, match=ScalingBlock.DECISION_TAMPERED.value):
        c.assert_integrity(d)


def test_missing_pool_is_blocked():
    _, c = setup()
    with pytest.raises(ValueError, match=ScalingBlock.POOL_NOT_FOUND.value):
        c.observe(ScalingObservation("o8", "ws-1", "missing", queue_depth=0, active_tasks=0, capacity_slots=1))
