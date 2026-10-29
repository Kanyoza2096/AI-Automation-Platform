import sqlite3
import pytest

from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_9_worker_selection import WorkerCapacityRegistry, WorkerSelectionRegistry
from services.v71_21_worker_registry import WorkerRegistry, WorkerKind, WorkerHealth, WorkerLifecycle
from services.v71_23_workforce_demand_planning import WorkforceDemandPlan, DemandDisposition
from services.v71_24_workforce_capacity_allocation import WorkforceCapacityAllocation
from services.v71_25_worker_assignment import WorkerAssignmentAdmission, AssignmentBlock


def setup(tmp_path):
    ws = "ws-1"
    identities = AgentIdentityRegistry(ws)
    ident = identities.register(agent_id="w1", name="Worker One", purpose="dispatch operations", declared_capabilities=("dispatch",))
    identities.transition(agent_id="w1", status=AgentStatus.ACTIVE)
    perms = AgentCapabilityPermissionRegistry(ws, identities)
    perms.grant(permission_id="perm-1", agent_id="w1", capability="dispatch", scope={})
    caps = WorkerCapacityRegistry(ws, identities)
    caps.register(agent_id="w1", max_concurrent_tasks=2, active_tasks=0)
    selection = WorkerSelectionRegistry(ws, identities, perms, caps)
    registry = WorkerRegistry(ws, identities, caps)
    registry.create_pool(pool_id="p1", name="Ops", worker_kind=WorkerKind.WORKFLOW,
                         capability_profile=("dispatch",), min_instances=1, desired_instances=1, max_instances=2)
    registry.register(worker_id="w1", pool_id="p1", name="Worker One", worker_kind=WorkerKind.WORKFLOW,
                      declared_capabilities=("dispatch",), health=WorkerHealth.HEALTHY, max_concurrent_tasks=2)
    registry.transition(worker_id="w1", lifecycle=WorkerLifecycle.READY)
    registry.transition(worker_id="w1", lifecycle=WorkerLifecycle.ACTIVE)
    selection.worker_registry = registry
    s = selection.select(selection_id="s1", task_id="t1", capability="dispatch")
    plan = WorkforceDemandPlan("pplan", ws, "p1", DemandDisposition.EXPAND, 1, 1, 1, 2,
                               ("test",), "policy", "obs", "", True, False, False, "")
    plan = WorkforceDemandPlan(**{**plan.__dict__, "plan_fingerprint": plan.fingerprint()})
    allocation = WorkforceCapacityAllocation(ws, registry, str(tmp_path / "alloc.db"))
    r = allocation.allocate(reservation_id="r1", plan=plan, requested_slots=1)
    adm = WorkerAssignmentAdmission(ws, registry, selection, allocation, str(tmp_path / "assign.db"))
    return ws, registry, selection, allocation, adm, s, r


def test_assignment_binds_selection_and_capacity(tmp_path):
    *_, adm, s, r = setup(tmp_path)
    a = adm.assign(assignment_id="a1", task_id="t1", selection_id=s.selection_id, reservation_id=r.reservation_id)
    assert a.worker_id == "w1"
    assert a.coordination_only is True
    assert a.authorization_granted is False
    assert a.execution_requested is False
    adm.assert_integrity(a)


def test_task_mismatch_is_rejected(tmp_path):
    *_, adm, s, r = setup(tmp_path)
    with pytest.raises(ValueError, match=AssignmentBlock.TASK_MISMATCH.value):
        adm.assign(assignment_id="a1", task_id="other", selection_id=s.selection_id, reservation_id=r.reservation_id)


def test_missing_capacity_is_rejected(tmp_path):
    *_, adm, s, _ = setup(tmp_path)
    with pytest.raises(ValueError, match=AssignmentBlock.CAPACITY_MISSING.value):
        adm.assign(assignment_id="a1", task_id="t1", selection_id=s.selection_id, reservation_id="missing")


def test_hold_capacity_cannot_assign(tmp_path):
    ws, registry, selection, allocation, adm, s, r = setup(tmp_path)
    # Use a fresh allocation with a HOLD plan.
    plan = WorkforceDemandPlan("hold", ws, "p1", DemandDisposition.HOLD, 1, 1, 1, 2,
                               ("hold",), "policy", "obs", "", True, False, False, "")
    plan = WorkforceDemandPlan(**{**plan.__dict__, "plan_fingerprint": plan.fingerprint()})
    hold = allocation.allocate(reservation_id="hold-r", plan=plan, requested_slots=1)
    with pytest.raises(ValueError, match=AssignmentBlock.CAPACITY_INSUFFICIENT.value):
        adm.assign(assignment_id="a1", task_id="t1", selection_id=s.selection_id, reservation_id=hold.reservation_id)


def test_worker_must_remain_active(tmp_path):
    *_, registry, selection, allocation, adm, s, r = setup(tmp_path)
    registry.transition(worker_id="w1", lifecycle=WorkerLifecycle.DRAINING)
    with pytest.raises(ValueError, match=AssignmentBlock.WORKER_NOT_ACTIVE.value):
        adm.assign(assignment_id="a1", task_id="t1", selection_id=s.selection_id, reservation_id=r.reservation_id)


def test_pool_mismatch_is_rejected(tmp_path):
    ws, registry, selection, allocation, adm, s, r = setup(tmp_path)
    # Tamper the persisted reservation's pool identity and fingerprint.
    allocation._db.execute("UPDATE workforce_capacity_reservations SET payload=json_set(payload, '$.pool_id', 'other') WHERE reservation_id='r1'")
    allocation._db.commit()
    with pytest.raises(ValueError, match="allocation_tampered"):
        adm.assign(assignment_id="a1", task_id="t1", selection_id=s.selection_id, reservation_id=r.reservation_id)


def test_duplicate_assignment_is_rejected(tmp_path):
    *_, adm, s, r = setup(tmp_path)
    adm.assign(assignment_id="a1", task_id="t1", selection_id=s.selection_id, reservation_id=r.reservation_id)
    with pytest.raises(ValueError, match=AssignmentBlock.DUPLICATE.value):
        adm.assign(assignment_id="a1", task_id="t1", selection_id=s.selection_id, reservation_id=r.reservation_id)


def test_restart_reads_assignment(tmp_path):
    ws, registry, selection, allocation, adm, s, r = setup(tmp_path)
    # Persist using a shared assignment database.
    db = str(tmp_path / "restart.db")
    adm.close()
    adm = WorkerAssignmentAdmission(ws, registry, selection, allocation, db)
    a = adm.assign(assignment_id="a1", task_id="t1", selection_id=s.selection_id, reservation_id=r.reservation_id)
    adm.close()
    adm2 = WorkerAssignmentAdmission(ws, registry, selection, allocation, db)
    restored = adm2.get("a1")
    assert restored is not None
    adm2.assert_integrity(restored)


def test_tampered_assignment_fails_closed(tmp_path):
    *_, adm, s, r = setup(tmp_path)
    a = adm.assign(assignment_id="a1", task_id="t1", selection_id=s.selection_id, reservation_id=r.reservation_id)
    tampered = type(a)(**{**a.__dict__, "worker_id": "evil"})
    with pytest.raises(ValueError, match=AssignmentBlock.ASSIGNMENT_TAMPERED.value):
        adm.assert_integrity(tampered)


def test_context_has_no_execution_authority(tmp_path):
    *_, adm, s, r = setup(tmp_path)
    adm.assign(assignment_id="a1", task_id="t1", selection_id=s.selection_id, reservation_id=r.reservation_id)
    ctx = adm.context("a1")
    assert ctx["coordination_only"] is True
    assert ctx["authorization_granted"] is False
    assert ctx["execution_requested"] is False
