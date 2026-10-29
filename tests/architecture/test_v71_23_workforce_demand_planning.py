from services.v71_23_workforce_demand_planning import WorkforceDemandPlanner, DemandForecast, DemandDisposition, WorkforceDemandPlan
from services.v71_22_workforce_scaling import WorkforceScalingCoordinator, ScalingPolicy, ScalingObservation
from services.v71_21_worker_registry import WorkerRegistry, WorkerKind
from services.v71_2_agent_capability_permissions import AgentIdentityRegistry


def setup():
    ids = AgentIdentityRegistry("ws-1")
    # use the existing registry test helper surface where available
    identity = ids.register(agent_id="w1", name="Worker 1", purpose="workforce")
    ids.transition(agent_id="w1", status="active")
    reg = WorkerRegistry("ws-1", ids)
    reg.create_pool(pool_id="pool", name="Pool", worker_kind=WorkerKind.AI, min_instances=1, desired_instances=1, max_instances=5)
    reg.register(worker_id="w1", pool_id="pool", name="Worker 1", worker_kind=WorkerKind.AI, declared_capabilities=(), max_concurrent_tasks=4)
    reg.transition(worker_id="w1", lifecycle="ready")
    reg.transition(worker_id="w1", lifecycle="active")
    scaling = WorkforceScalingCoordinator("ws-1", reg)
    policy = scaling.register_policy(ScalingPolicy("p", "ws-1", 1, 5, max_step=2))
    obs = scaling.observe(ScalingObservation("o", "ws-1", "pool", 0, 1, 4))
    return reg, scaling, policy, obs


def test_planner_is_advisory_only():
    assert not any(x in " ".join(dir(WorkforceDemandPlanner)).lower() for x in ("execute", "provision", "authorize"))
    assert WorkforceDemandPlan.__dataclass_fields__["advisory_only"]


def test_stable_plan():
    reg, scaling, policy, obs = setup()
    planner = WorkforceDemandPlanner("ws-1", reg, scaling)
    plan = planner.plan(plan_id="plan", policy=policy, observation=obs)
    assert plan.disposition == DemandDisposition.STABLE
    planner.assert_integrity(plan)


def test_forecast_pressure_expands_within_bounds():
    reg, scaling, policy, obs = setup()
    planner = WorkforceDemandPlanner("ws-1", reg, scaling)
    f = planner.record_forecast(DemandForecast("f", "ws-1", "pool", 3, 4, 2, .9))
    plan = planner.plan(plan_id="plan", policy=policy, observation=obs, forecast_id=f.forecast_id)
    assert plan.disposition == DemandDisposition.EXPAND
    assert plan.planned_instances <= 5


def test_low_forecast_confidence_holds():
    reg, scaling, policy, obs = setup()
    planner = WorkforceDemandPlanner("ws-1", reg, scaling)
    f = planner.record_forecast(DemandForecast("f", "ws-1", "pool", 10, 10, 2, .4))
    plan = planner.plan(plan_id="plan", policy=policy, observation=obs, forecast_id=f.forecast_id)
    assert plan.disposition == DemandDisposition.HOLD


def test_safety_signal_holds():
    reg, scaling, policy, _ = setup()
    planner = WorkforceDemandPlanner("ws-1", reg, scaling)
    obs = scaling.observe(ScalingObservation("o", "ws-1", "pool", 10, 4, 4, emergency_stop=True))
    plan = planner.plan(plan_id="plan", policy=policy, observation=obs)
    assert plan.disposition == DemandDisposition.HOLD


def test_tampering_detected():
    reg, scaling, policy, obs = setup()
    planner = WorkforceDemandPlanner("ws-1", reg, scaling)
    plan = planner.plan(plan_id="plan", policy=policy, observation=obs)
    forged = WorkforceDemandPlan(**{**plan.__dict__, "planned_instances": 99})
    try:
        planner.assert_integrity(forged)
        assert False
    except ValueError:
        pass
