import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_9_worker_selection import WorkerCapacityRegistry, WorkerAvailability
from services.v71_21_worker_registry import (
    WorkerRegistry, WorkerKind, WorkerLifecycle, WorkerHealth, WorkerRegistryBlock,
)


def setup():
    ids = AgentIdentityRegistry("ws-1")
    ids.register(agent_id="w1", name="Security Worker 1", purpose="security operations", declared_capabilities=("security.inspect", "security.contain"))
    ids.register(agent_id="w2", name="Security Worker 2", purpose="security operations", declared_capabilities=("security.inspect",))
    cap = WorkerCapacityRegistry("ws-1", ids)
    reg = WorkerRegistry("ws-1", ids, cap)
    reg.create_pool(pool_id="security", name="Security Operations", worker_kind=WorkerKind.AI, capability_profile=("security.inspect",), min_instances=1, desired_instances=2, max_instances=5)
    return ids, cap, reg


def test_registers_worker_instance_against_pool_and_identity():
    ids, cap, reg = setup()
    cap.register(agent_id="w1", max_concurrent_tasks=2, autonomy_level=2)
    w = reg.register(worker_id="w1", pool_id="security", name="Security Worker 1", worker_kind=WorkerKind.AI, declared_capabilities=("security.inspect", "security.contain"), health=WorkerHealth.HEALTHY)
    assert w.lifecycle == WorkerLifecycle.REGISTERED
    assert w.pool_id == "security"
    assert w.registry_fingerprint == w.fingerprint()


def test_activation_requires_active_identity_and_capacity():
    ids, cap, reg = setup()
    cap.register(agent_id="w1", max_concurrent_tasks=2)
    reg.register(worker_id="w1", pool_id="security", name="w1", worker_kind=WorkerKind.AI, declared_capabilities=("security.inspect",), health=WorkerHealth.HEALTHY)
    with pytest.raises(ValueError, match=WorkerRegistryBlock.IDENTITY_NOT_ACTIVE.value):
        reg.transition(worker_id="w1", lifecycle=WorkerLifecycle.READY)
    ids.transition(agent_id="w1", status=AgentStatus.ACTIVE)
    reg.transition(worker_id="w1", lifecycle=WorkerLifecycle.READY)
    w = reg.transition(worker_id="w1", lifecycle=WorkerLifecycle.ACTIVE)
    assert w.lifecycle == WorkerLifecycle.ACTIVE


def test_unavailable_health_cannot_activate():
    ids, cap, reg = setup(); ids.transition(agent_id="w1", status=AgentStatus.ACTIVE)
    cap.register(agent_id="w1", max_concurrent_tasks=1)
    reg.register(worker_id="w1", pool_id="security", name="w1", worker_kind=WorkerKind.AI, declared_capabilities=("security.inspect",), health=WorkerHealth.UNAVAILABLE)
    with pytest.raises(ValueError, match=WorkerRegistryBlock.HEALTH_HOLD.value):
        reg.transition(worker_id="w1", lifecycle=WorkerLifecycle.READY)


def test_health_propagates_to_capacity_availability():
    ids, cap, reg = setup(); ids.transition(agent_id="w1", status=AgentStatus.ACTIVE)
    cap.register(agent_id="w1")
    reg.register(worker_id="w1", pool_id="security", name="w1", worker_kind=WorkerKind.AI, declared_capabilities=("security.inspect",), health=WorkerHealth.HEALTHY)
    reg.set_health(worker_id="w1", health=WorkerHealth.UNAVAILABLE)
    assert cap.get("w1").availability == WorkerAvailability.UNAVAILABLE


def test_draining_is_not_active_selection_state():
    ids, cap, reg = setup(); ids.transition(agent_id="w1", status=AgentStatus.ACTIVE)
    cap.register(agent_id="w1")
    reg.register(worker_id="w1", pool_id="security", name="w1", worker_kind=WorkerKind.AI, declared_capabilities=("security.inspect",), health=WorkerHealth.HEALTHY)
    reg.transition(worker_id="w1", lifecycle=WorkerLifecycle.READY)
    reg.transition(worker_id="w1", lifecycle=WorkerLifecycle.ACTIVE)
    w = reg.transition(worker_id="w1", lifecycle=WorkerLifecycle.DRAINING)
    assert not w.eligible_for_selection


def test_capacity_updates_are_fingerprinted_and_synchronized():
    ids, cap, reg = setup(); ids.transition(agent_id="w1", status=AgentStatus.ACTIVE)
    cap.register(agent_id="w1", max_concurrent_tasks=3)
    reg.register(worker_id="w1", pool_id="security", name="w1", worker_kind=WorkerKind.AI, declared_capabilities=("security.inspect",), health=WorkerHealth.HEALTHY, max_concurrent_tasks=3)
    w = reg.set_capacity(worker_id="w1", active_tasks=2)
    assert w.active_tasks == 2 and cap.get("w1").active_tasks == 2
    assert w.registry_fingerprint == w.fingerprint()


def test_tamper_detection_is_fail_closed():
    ids, cap, reg = setup(); ids.transition(agent_id="w1", status=AgentStatus.ACTIVE)
    cap.register(agent_id="w1")
    w = reg.register(worker_id="w1", pool_id="security", name="w1", worker_kind=WorkerKind.AI, declared_capabilities=("security.inspect",), health=WorkerHealth.HEALTHY)
    object.__setattr__(w, "name", "tampered")
    with pytest.raises(ValueError, match=WorkerRegistryBlock.WORKER_TAMPERED.value):
        reg.assert_integrity(w)


def test_cross_workspace_registry_is_blocked():
    ids = AgentIdentityRegistry("ws-1")
    with pytest.raises(ValueError, match=WorkerRegistryBlock.TENANT_MISMATCH.value):
        WorkerRegistry("ws-2", ids)


def test_pool_scaling_bounds_are_explicit():
    ids, cap, reg = setup()
    with pytest.raises(ValueError, match=WorkerRegistryBlock.INVALID_POOL.value):
        reg.create_pool(pool_id="bad", name="bad", worker_kind=WorkerKind.AI, min_instances=3, desired_instances=2, max_instances=2)


def test_pool_counts_and_dynamic_inventory_snapshot():
    ids, cap, reg = setup(); ids.transition(agent_id="w1", status=AgentStatus.ACTIVE); ids.transition(agent_id="w2", status=AgentStatus.ACTIVE)
    cap.register(agent_id="w1"); cap.register(agent_id="w2")
    for aid in ("w1", "w2"):
        reg.register(worker_id=aid, pool_id="security", name=aid, worker_kind=WorkerKind.AI, declared_capabilities=("security.inspect",), health=WorkerHealth.HEALTHY)
    reg.transition(worker_id="w1", lifecycle=WorkerLifecycle.READY); reg.transition(worker_id="w1", lifecycle=WorkerLifecycle.ACTIVE)
    counts = reg.pool_counts("security")
    snap = reg.selection_snapshot()
    assert counts[WorkerLifecycle.ACTIVE.value] == 1
    assert snap["worker_count"] == 2 and snap["active_count"] == 1
    assert snap["authority_granted"] is False and snap["execution_requested"] is False


def test_retirement_is_terminal_and_capacity_is_removed_from_eligibility():
    ids, cap, reg = setup(); ids.transition(agent_id="w1", status=AgentStatus.ACTIVE); cap.register(agent_id="w1")
    reg.register(worker_id="w1", pool_id="security", name="w1", worker_kind=WorkerKind.AI, declared_capabilities=("security.inspect",), health=WorkerHealth.HEALTHY)
    reg.transition(worker_id="w1", lifecycle=WorkerLifecycle.READY)
    w = reg.transition(worker_id="w1", lifecycle=WorkerLifecycle.RETIRED)
    assert w.lifecycle == WorkerLifecycle.RETIRED
    assert cap.get("w1").availability == WorkerAvailability.UNAVAILABLE
    with pytest.raises(ValueError, match=WorkerRegistryBlock.RETIRED.value):
        reg.transition(worker_id="w1", lifecycle=WorkerLifecycle.READY)


def test_selection_can_be_fenced_by_registry_lifecycle():
    from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
    from services.v71_9_worker_selection import WorkerSelectionRegistry
    ids, cap, reg = setup(); ids.transition(agent_id="w1", status=AgentStatus.ACTIVE)
    cap.register(agent_id="w1", max_concurrent_tasks=1, autonomy_level=2)
    perms = AgentCapabilityPermissionRegistry("ws-1", ids)
    perms.grant(permission_id="p1", agent_id="w1", capability="security.inspect", scope={"workspace_id":"ws-1"})
    reg.register(worker_id="w1", pool_id="security", name="w1", worker_kind=WorkerKind.AI, declared_capabilities=("security.inspect",), health=WorkerHealth.HEALTHY)
    selector = WorkerSelectionRegistry("ws-1", ids, perms, cap, worker_registry=reg)
    pending = selector.select(selection_id="s-pending", task_id="t1", capability="security.inspect", requested_scope={"workspace_id":"ws-1"})
    assert pending.selected_agent_id is None
    reg.transition(worker_id="w1", lifecycle=WorkerLifecycle.READY)
    reg.transition(worker_id="w1", lifecycle=WorkerLifecycle.ACTIVE)
    active = selector.select(selection_id="s-active", task_id="t2", capability="security.inspect", requested_scope={"workspace_id":"ws-1"})
    assert active.selected_agent_id == "w1"
