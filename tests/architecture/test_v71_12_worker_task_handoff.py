import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_9_worker_selection import WorkerCapacityRegistry, WorkerSelectionRegistry
from services.v71_21_worker_registry import WorkerRegistry, WorkerKind, WorkerHealth, WorkerLifecycle
from services.v71_23_workforce_demand_planning import WorkforceDemandPlan, DemandDisposition
from services.v71_24_workforce_capacity_allocation import WorkforceCapacityAllocation
from services.v71_25_worker_assignment import WorkerAssignmentAdmission
from services.v71_26_worker_assignment_lifecycle import WorkerAssignmentLifecycleRegistry
from services.v71_12_worker_task_handoff import WorkerTaskHandoffRegistry, HandoffState, HandoffBlock


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
    lifecycle.start(lifecycle_id="l1", assignment_id=a.assignment_id)
    handoff = WorkerTaskHandoffRegistry(ws, admission, lifecycle, str(tmp_path / "handoff.db"))
    return handoff, a


def test_offer_and_acknowledge(tmp_path):
    handoff, a = setup(tmp_path)
    offered = handoff.offer(handoff_id="h1", assignment_id=a.assignment_id)
    assert offered.state is HandoffState.OFFERED
    ack = handoff.acknowledge(handoff_id="h2", assignment_id=a.assignment_id)
    assert ack.state is HandoffState.ACKNOWLEDGED
    handoff.assert_integrity(ack)


def test_decline_requires_reason(tmp_path):
    handoff, a = setup(tmp_path)
    handoff.offer(handoff_id="h1", assignment_id=a.assignment_id)
    with pytest.raises(ValueError, match=HandoffBlock.TERMINAL_REASON_REQUIRED.value):
        handoff.decline(handoff_id="h2", assignment_id=a.assignment_id, reason="")


def test_decline_from_offer(tmp_path):
    handoff, a = setup(tmp_path)
    handoff.offer(handoff_id="h1", assignment_id=a.assignment_id)
    r = handoff.decline(handoff_id="h2", assignment_id=a.assignment_id, reason="worker_unavailable")
    assert r.state is HandoffState.DECLINED


def test_duplicate_and_invalid_transition_fail_closed(tmp_path):
    handoff, a = setup(tmp_path)
    handoff.offer(handoff_id="h1", assignment_id=a.assignment_id)
    with pytest.raises(ValueError, match=HandoffBlock.INVALID_TRANSITION.value):
        handoff.offer(handoff_id="h2", assignment_id=a.assignment_id)
    with pytest.raises(ValueError, match=HandoffBlock.DUPLICATE.value):
        handoff.offer(handoff_id="h1", assignment_id=a.assignment_id)


def test_requires_active_assignment_lifecycle(tmp_path):
    handoff, a = setup(tmp_path)
    # use a fresh registry with the same assignment but no lifecycle record
    from services.v71_26_worker_assignment_lifecycle import WorkerAssignmentLifecycleRegistry
    life2 = WorkerAssignmentLifecycleRegistry("ws-1", handoff.admission, handoff.lifecycle.capacity_registry, str(tmp_path / "otherlife.db"))
    h2 = WorkerTaskHandoffRegistry("ws-1", handoff.admission, life2, str(tmp_path / "otherhandoff.db"))
    with pytest.raises(ValueError, match=HandoffBlock.LIFECYCLE_MISSING.value):
        h2.offer(handoff_id="hx", assignment_id=a.assignment_id)


def test_tamper_fails_closed(tmp_path):
    handoff, a = setup(tmp_path)
    r = handoff.offer(handoff_id="h1", assignment_id=a.assignment_id)
    tampered = type(r)(**{**r.__dict__, "worker_id": "evil"})
    with pytest.raises(ValueError, match=HandoffBlock.HANDOFF_TAMPERED.value):
        handoff.assert_integrity(tampered)


def test_restart_rehydrates(tmp_path):
    handoff, a = setup(tmp_path)
    db = str(tmp_path / "restart.db")
    from services.v71_12_worker_task_handoff import WorkerTaskHandoffRegistry
    persistent = WorkerTaskHandoffRegistry("ws-1", handoff.admission, handoff.lifecycle, db)
    r = persistent.offer(handoff_id="persisted", assignment_id=a.assignment_id)
    persistent.close()
    restored = WorkerTaskHandoffRegistry("ws-1", handoff.admission, handoff.lifecycle, db)
    found = restored.get("persisted")
    assert found is not None
    assert found.handoff_fingerprint == r.handoff_fingerprint
    restored.assert_integrity(found)
    restored.close()


def test_workspace_fence(tmp_path):
    handoff, a = setup(tmp_path)
    foreign = type(a)(**{**a.__dict__, "workspace_id": "ws-evil"})
    with pytest.raises(ValueError):
        handoff.admission.assert_integrity(foreign)


def test_no_authority_or_provider_side_effect(tmp_path):
    handoff, a = setup(tmp_path)
    r = handoff.offer(handoff_id="h1", assignment_id=a.assignment_id)
    ctx = handoff.context(a.assignment_id)
    assert r.coordination_only is True
    assert r.authorization_granted is False
    assert r.execution_requested is False
    assert r.provider_called is False
    assert ctx["execution_requested"] is False


def test_acknowledged_handoff_is_terminal(tmp_path):
    handoff, a = setup(tmp_path)
    handoff.offer(handoff_id="h1", assignment_id=a.assignment_id)
    handoff.acknowledge(handoff_id="h2", assignment_id=a.assignment_id)
    with pytest.raises(ValueError, match=HandoffBlock.INVALID_TRANSITION.value):
        handoff.cancel(handoff_id="h3", assignment_id=a.assignment_id, reason="operator_cancelled")
