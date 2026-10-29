import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_9_worker_selection import WorkerCapacityRegistry, WorkerSelectionRegistry
from services.v71_21_worker_registry import WorkerRegistry, WorkerKind, WorkerHealth, WorkerLifecycle
from services.v71_23_workforce_demand_planning import WorkforceDemandPlan, DemandDisposition
from services.v71_24_workforce_capacity_allocation import WorkforceCapacityAllocation
from services.v71_25_worker_assignment import WorkerAssignmentAdmission
from services.v71_26_worker_assignment_lifecycle import WorkerAssignmentLifecycleRegistry, AssignmentLifecycleState, LifecycleBlock


def setup(tmp_path):
    ws = "ws-1"
    identities = AgentIdentityRegistry(ws)
    identities.register(agent_id="w1", name="Worker One", purpose="dispatch operations", declared_capabilities=("dispatch",))
    identities.transition(agent_id="w1", status=AgentStatus.ACTIVE)
    perms = AgentCapabilityPermissionRegistry(ws, identities)
    perms.grant(permission_id="perm-1", agent_id="w1", capability="dispatch", scope={})
    caps = WorkerCapacityRegistry(ws, identities)
    caps.register(agent_id="w1", max_concurrent_tasks=1, active_tasks=0)
    selection = WorkerSelectionRegistry(ws, identities, perms, caps)
    registry = WorkerRegistry(ws, identities, caps)
    registry.create_pool(pool_id="p1", name="Ops", worker_kind=WorkerKind.WORKFLOW, capability_profile=("dispatch",), min_instances=1, desired_instances=1, max_instances=2)
    registry.register(worker_id="w1", pool_id="p1", name="Worker One", worker_kind=WorkerKind.WORKFLOW, declared_capabilities=("dispatch",), health=WorkerHealth.HEALTHY, max_concurrent_tasks=1)
    registry.transition(worker_id="w1", lifecycle=WorkerLifecycle.READY)
    registry.transition(worker_id="w1", lifecycle=WorkerLifecycle.ACTIVE)
    selection.worker_registry = registry
    s = selection.select(selection_id="s1", task_id="t1", capability="dispatch")
    plan = WorkforceDemandPlan("pplan", ws, "p1", DemandDisposition.EXPAND, 1, 1, 1, 2, ("test",), "policy", "obs", "", True, False, False, "")
    plan = WorkforceDemandPlan(**{**plan.__dict__, "plan_fingerprint": plan.fingerprint()})
    allocation = WorkforceCapacityAllocation(ws, registry, str(tmp_path / "alloc.db"))
    r = allocation.allocate(reservation_id="r1", plan=plan, requested_slots=1)
    admission = WorkerAssignmentAdmission(ws, registry, selection, allocation, str(tmp_path / "assign.db"))
    a = admission.assign(assignment_id="a1", task_id="t1", selection_id=s.selection_id, reservation_id=r.reservation_id)
    lifecycle = WorkerAssignmentLifecycleRegistry(ws, admission, caps, str(tmp_path / "life.db"))
    return caps, lifecycle, a


def test_start_increments_logical_capacity(tmp_path):
    caps, life, a = setup(tmp_path)
    r = life.start(lifecycle_id="l1", assignment_id=a.assignment_id)
    assert r.state is AssignmentLifecycleState.ACTIVE
    assert caps.get("w1").active_tasks == 1
    life.assert_integrity(r)


def test_second_start_is_rejected(tmp_path):
    _, life, a = setup(tmp_path)
    life.start(lifecycle_id="l1", assignment_id=a.assignment_id)
    with pytest.raises(ValueError, match=LifecycleBlock.INVALID_TRANSITION.value):
        life.start(lifecycle_id="l2", assignment_id=a.assignment_id)


def test_release_decrements_capacity(tmp_path):
    caps, life, a = setup(tmp_path)
    life.start(lifecycle_id="l1", assignment_id=a.assignment_id)
    r = life.release(lifecycle_id="l2", assignment_id=a.assignment_id, reason="task_terminal")
    assert r.state is AssignmentLifecycleState.RELEASED
    assert caps.get("w1").active_tasks == 0


def test_cancel_requires_active_assignment(tmp_path):
    _, life, a = setup(tmp_path)
    with pytest.raises(ValueError, match=LifecycleBlock.INVALID_TRANSITION.value):
        life.cancel(lifecycle_id="l1", assignment_id=a.assignment_id, reason="operator_cancelled")


def test_terminal_reason_required(tmp_path):
    _, life, a = setup(tmp_path)
    life.start(lifecycle_id="l1", assignment_id=a.assignment_id)
    with pytest.raises(ValueError, match=LifecycleBlock.TERMINAL_REASON_REQUIRED.value):
        life.release(lifecycle_id="l2", assignment_id=a.assignment_id, reason="")


def test_capacity_limit_is_fail_closed(tmp_path):
    caps, life, a = setup(tmp_path)
    caps.update_load("w1", active_tasks=1, load_score=1.0)
    with pytest.raises(ValueError, match=LifecycleBlock.CAPACITY_INVALID.value):
        life.start(lifecycle_id="l1", assignment_id=a.assignment_id)


def test_restart_rehydrates_lifecycle(tmp_path):
    caps, life, a = setup(tmp_path)
    db = str(tmp_path / "restart.db")
    life.close()
    life = WorkerAssignmentLifecycleRegistry("ws-1", life.admission, caps, db)
    life.start(lifecycle_id="l1", assignment_id=a.assignment_id)
    life.close()
    life2 = WorkerAssignmentLifecycleRegistry("ws-1", life.admission, caps, db)
    restored = life2.get("l1")
    assert restored is not None
    life2.assert_integrity(restored)


def test_tamper_fails_closed(tmp_path):
    _, life, a = setup(tmp_path)
    r = life.start(lifecycle_id="l1", assignment_id=a.assignment_id)
    tampered = type(r)(**{**r.__dict__, "worker_id": "evil"})
    with pytest.raises(ValueError, match=LifecycleBlock.LIFECYCLE_TAMPERED.value):
        life.assert_integrity(tampered)


def test_workspace_fence(tmp_path):
    _, life, a = setup(tmp_path)
    foreign = type(a)(**{**a.__dict__, "workspace_id": "ws-evil"})
    with pytest.raises(ValueError):
        life.admission.assert_integrity(foreign)


def test_lifecycle_never_observes_business_outcome_or_authority(tmp_path):
    _, life, a = setup(tmp_path)
    r = life.start(lifecycle_id="l1", assignment_id=a.assignment_id)
    ctx = life.context(a.assignment_id)
    assert r.coordination_only is True
    assert r.authorization_granted is False
    assert r.execution_requested is False
    assert r.business_outcome_observed is False
    assert ctx["business_outcome_observed"] is False
