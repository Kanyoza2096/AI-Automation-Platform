import tempfile
from services.v71_24_workforce_capacity_allocation import WorkforceCapacityAllocation, AllocationDisposition, CapacityReservation
from services.v71_23_workforce_demand_planning import WorkforceDemandPlanner, DemandDisposition
from services.v71_22_workforce_scaling import WorkforceScalingCoordinator, ScalingPolicy, ScalingObservation
from services.v71_21_worker_registry import WorkerRegistry, WorkerKind
from services.v71_2_agent_capability_permissions import AgentIdentityRegistry


def setup(db=":memory:"):
    ids = AgentIdentityRegistry("ws-1")
    ids.register(agent_id="w1", name="Worker 1", purpose="workforce")
    ids.transition(agent_id="w1", status="active")
    reg = WorkerRegistry("ws-1", ids)
    reg.create_pool(pool_id="pool", name="Pool", worker_kind=WorkerKind.AI, min_instances=1, desired_instances=1, max_instances=5)
    reg.register(worker_id="w1", pool_id="pool", name="Worker 1", worker_kind=WorkerKind.AI, declared_capabilities=(), max_concurrent_tasks=4)
    reg.transition(worker_id="w1", lifecycle="ready")
    reg.transition(worker_id="w1", lifecycle="active")
    scaling = WorkforceScalingCoordinator("ws-1", reg)
    policy = scaling.register_policy(ScalingPolicy("p", "ws-1", 1, 5, max_step=2))
    obs = scaling.observe(ScalingObservation("o", "ws-1", "pool", 0, 1, 4))
    plan = WorkforceDemandPlanner("ws-1", reg, scaling).plan(plan_id="plan", policy=policy, observation=obs)
    return reg, plan, db


def test_reserves_bounded_logical_capacity():
    reg, plan, db = setup()
    a = WorkforceCapacityAllocation("ws-1", reg, db)
    r = a.allocate(reservation_id="r1", plan=plan, requested_slots=3)
    assert r.disposition == AllocationDisposition.RESERVE and r.reserved_slots == 3 and r.available_slots == 4
    a.assert_integrity(r)


def test_insufficient_capacity_holds():
    reg, plan, db = setup()
    a = WorkforceCapacityAllocation("ws-1", reg, db)
    r = a.allocate(reservation_id="r1", plan=plan, requested_slots=5)
    assert r.disposition == AllocationDisposition.HOLD and r.reserved_slots == 0


def test_demand_hold_propagates():
    reg, plan, db = setup()
    from dataclasses import replace
    held = replace(plan, disposition=DemandDisposition.HOLD)
    held = replace(held, plan_fingerprint=held.fingerprint())
    a = WorkforceCapacityAllocation("ws-1", reg, db)
    r = a.allocate(reservation_id="r1", plan=held, requested_slots=1)
    assert r.disposition == AllocationDisposition.HOLD


def test_zero_request_is_stable():
    reg, plan, db = setup()
    a = WorkforceCapacityAllocation("ws-1", reg, db)
    r = a.allocate(reservation_id="r1", plan=plan, requested_slots=0)
    assert r.disposition == AllocationDisposition.STABLE


def test_safety_hold_blocks_reservation():
    reg, plan, db = setup()
    a = WorkforceCapacityAllocation("ws-1", reg, db)
    r = a.allocate(reservation_id="r1", plan=plan, requested_slots=1, safety_hold=True)
    assert r.disposition == AllocationDisposition.HOLD


def test_workspace_fence():
    reg, plan, db = setup()
    from dataclasses import replace
    bad = replace(plan, workspace_id="other")
    a = WorkforceCapacityAllocation("ws-1", reg, db)
    try:
        a.allocate(reservation_id="r1", plan=bad, requested_slots=1)
        assert False
    except ValueError:
        pass


def test_tampering_detected():
    reg, plan, db = setup()
    a = WorkforceCapacityAllocation("ws-1", reg, db)
    r = a.allocate(reservation_id="r1", plan=plan, requested_slots=2)
    forged = CapacityReservation(**{**r.__dict__, "reserved_slots": 4})
    try:
        a.assert_integrity(forged)
        assert False
    except ValueError:
        pass


def test_restart_reads_same_reservation():
    reg, plan, _ = setup()
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        a = WorkforceCapacityAllocation("ws-1", reg, f.name)
        r = a.allocate(reservation_id="r1", plan=plan, requested_slots=2)
        a.close()
        b = WorkforceCapacityAllocation("ws-1", reg, f.name)
        loaded = b.get("r1")
        assert loaded == r
        b.assert_integrity(loaded)
        b.close()


def test_duplicate_reservation_rejected():
    reg, plan, db = setup()
    a = WorkforceCapacityAllocation("ws-1", reg, db)
    a.allocate(reservation_id="r1", plan=plan, requested_slots=1)
    try:
        a.allocate(reservation_id="r1", plan=plan, requested_slots=1)
        assert False
    except ValueError:
        pass


def test_no_execution_authority_surface():
    assert not any(x in " ".join(dir(WorkforceCapacityAllocation)).lower() for x in ("execute", "authorize", "provision"))
    assert CapacityReservation.__dataclass_fields__["assignment_authority_granted"]
