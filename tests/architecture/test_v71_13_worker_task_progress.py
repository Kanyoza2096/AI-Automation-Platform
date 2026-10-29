import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_9_worker_selection import WorkerCapacityRegistry, WorkerSelectionRegistry
from services.v71_21_worker_registry import WorkerRegistry, WorkerKind, WorkerHealth, WorkerLifecycle
from services.v71_23_workforce_demand_planning import WorkforceDemandPlan, DemandDisposition
from services.v71_24_workforce_capacity_allocation import WorkforceCapacityAllocation
from services.v71_25_worker_assignment import WorkerAssignmentAdmission
from services.v71_26_worker_assignment_lifecycle import WorkerAssignmentLifecycleRegistry
from services.v71_12_worker_task_handoff import WorkerTaskHandoffRegistry
from services.v71_13_worker_task_progress import WorkerTaskProgressRegistry, ProgressState, ProgressBlock


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
    h = handoff.offer(handoff_id="h1", assignment_id=a.assignment_id)
    h = handoff.acknowledge(handoff_id="h2", assignment_id=a.assignment_id)
    progress = WorkerTaskProgressRegistry(ws, handoff, str(tmp_path / "progress.db"))
    return progress, h


def test_progress_lifecycle_and_sequence(tmp_path):
    p, h = setup(tmp_path)
    assert p.start(progress_id="p1", handoff_id=h.handoff_id).state is ProgressState.STARTED
    assert p.report_progress(progress_id="p2", handoff_id=h.handoff_id, message="50%", evidence={"percent": 50}).sequence == 2
    assert p.submit(progress_id="p3", handoff_id=h.handoff_id, message="work_submitted").state is ProgressState.SUBMITTED
    assert [x.sequence for x in p.history(h.handoff_id)] == [1, 2, 3]


def test_requires_acknowledged_handoff(tmp_path):
    # Construct only enough setup to obtain an offered handoff.
    p, h = setup(tmp_path)
    with pytest.raises(ValueError):
        p.record(progress_id="bad", handoff_id="missing", state=ProgressState.STARTED, message="x", evidence={})


def test_terminal_submission_blocks_more_progress(tmp_path):
    p, h = setup(tmp_path)
    p.submit(progress_id="p1", handoff_id=h.handoff_id, message="submitted")
    with pytest.raises(ValueError, match=ProgressBlock.INVALID_TRANSITION.value):
        p.report_progress(progress_id="p2", handoff_id=h.handoff_id, message="late")


def test_tamper_cannot_assert_business_outcome(tmp_path):
    p, h = setup(tmp_path)
    r = p.start(progress_id="p1", handoff_id=h.handoff_id)
    tampered = type(r)(**{**r.__dict__, "business_outcome_asserted": True})
    with pytest.raises(ValueError, match=ProgressBlock.PROGRESS_TAMPERED.value):
        p.assert_integrity(tampered)


def test_restart_rehydrates_and_preserves_fingerprint(tmp_path):
    p, h = setup(tmp_path)
    db = str(tmp_path / "restart.db")
    persistent = WorkerTaskProgressRegistry("ws-1", p.handoffs, db)
    r = persistent.start(progress_id="persisted", handoff_id=h.handoff_id)
    persistent.close()
    restored = WorkerTaskProgressRegistry("ws-1", p.handoffs, db)
    found = restored.get("persisted")
    assert found and found.progress_fingerprint == r.progress_fingerprint
    restored.assert_integrity(found)
    restored.close()


def test_evidence_size_is_bounded(tmp_path):
    p, h = setup(tmp_path)
    with pytest.raises(ValueError, match=ProgressBlock.PAYLOAD_TOO_LARGE.value):
        p.start(progress_id="large", handoff_id=h.handoff_id, evidence={"blob": "x" * (p.MAX_EVIDENCE_BYTES + 1)})


def test_authority_is_never_granted(tmp_path):
    p, h = setup(tmp_path)
    r = p.start(progress_id="p1", handoff_id=h.handoff_id)
    ctx = p.context(h.handoff_id)
    assert r.coordination_only is True
    assert r.authorization_granted is False
    assert r.execution_requested is False
    assert r.provider_called is False
    assert r.business_outcome_asserted is False
    assert ctx["business_outcome_asserted"] is False


def test_workspace_fence(tmp_path):
    p, h = setup(tmp_path)
    foreign = type(h)(**{**h.__dict__, "workspace_id": "ws-evil"})
    with pytest.raises(ValueError):
        p.handoffs.assert_integrity(foreign)
