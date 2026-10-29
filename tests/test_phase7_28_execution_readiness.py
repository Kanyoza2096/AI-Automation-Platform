import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_9_worker_selection import WorkerSelectionRegistry, WorkerCapacityRegistry
from services.v71_21_worker_registry import WorkerRegistry, WorkerKind, WorkerLifecycle, WorkerHealth
from services.v71_24_workforce_capacity_allocation import WorkforceCapacityAllocation
from services.v71_35_worker_capability_qualification import WorkerCapabilityQualificationRegistry
from services.v71_37_workforce_multi_worker_planning import CoordinationTaskSpec, WorkforceMultiWorkerPlanner
from services.v71_38_workforce_resource_cost_coordination import WorkforceResourceCostCoordinator, ResourceCostPolicy, ResourceCostEstimate
from services.v71_39_workforce_execution_readiness import *
from tests.test_phase7_26_multi_worker_planning import FakeSelector, FakeRep


def setup(tmp_path):
    ws='ws'
    q=WorkerCapabilityQualificationRegistry(ws,FakeRep())
    q.certify(qualification_id='q-w1',worker_id='w1',capability='security',qualified_by='governance',issued_at='now')
    planner=WorkforceMultiWorkerPlanner(ws,FakeSelector(),q)
    plan=planner.plan(plan_id='p1',objective_id='o1',cycle_id='c1',coordinator_worker_id='coord',resource_budget=1,
                      specs=(CoordinationTaskSpec('a','security','contain'),))
    cost=WorkforceResourceCostCoordinator(ws,planner)
    cp=cost.evaluate(plan=plan,policy=ResourceCostPolicy(max_total_cost=10,max_total_resource_units=2),
                     estimates=(ResourceCostEstimate('a',1,1),))

    identity=AgentIdentityRegistry(ws)
    identity.register(agent_id='w1',name='Worker 1',purpose='security work',declared_capabilities=('security',))
    identity.transition(agent_id='w1',status=AgentStatus.ACTIVE)
    capacity=WorkerCapacityRegistry(ws,identity)
    capacity.register(agent_id='w1',max_concurrent_tasks=2)
    registry=WorkerRegistry(ws,identity,capacity)
    registry.create_pool(pool_id='pool1',name='Security',worker_kind=WorkerKind.DETERMINISTIC,max_instances=2)
    worker=registry.register(worker_id='w1',pool_id='pool1',name='Worker 1',worker_kind=WorkerKind.DETERMINISTIC,declared_capabilities=('security',))
    # Register defaults to REGISTERED; activate and mark healthy through registry APIs.
    registry.transition(worker_id='w1',lifecycle=WorkerLifecycle.READY)
    registry.transition(worker_id='w1',lifecycle=WorkerLifecycle.ACTIVE)
    registry.set_health(worker_id='w1',health=WorkerHealth.HEALTHY)
    allocation=WorkforceCapacityAllocation(ws,registry)
    # Build the minimum valid demand-plan-like reservation using the existing model.
    from services.v71_23_workforce_demand_planning import WorkforceDemandPlan, DemandDisposition
    demand=WorkforceDemandPlan('d1',ws,'pool1',DemandDisposition.EXPAND,0,1,0,2,('test_reservation',),'policy','observation','forecast',True,False,False,'')
    demand=WorkforceDemandPlan(**{**demand.__dict__,'plan_fingerprint':demand.fingerprint()})
    reservation=allocation.allocate(reservation_id='r1',plan=demand,requested_slots=1)
    readiness=WorkforceExecutionReadinessRegistry(ws,planner,cost,registry,allocation)
    return planner,plan,cost,cp,registry,allocation,reservation,readiness


def test_ready_plan_is_pre_admission_only(tmp_path):
    _,plan,_,cp,_,_,reservation,r=setup(tmp_path)
    out=r.evaluate(readiness_id='rd1',plan=plan,cost_plan=cp,reservation_ids={'a':reservation.reservation_id})
    assert out.disposition is ReadinessDisposition.READY
    assert r.context('rd1')['authority']=='none'
    assert out.assignments_created is False


def test_infeasible_cost_plan_cannot_become_ready(tmp_path):
    planner,plan,cost,_,registry,allocation,reservation,r=setup(tmp_path)
    from services.v71_38_workforce_resource_cost_coordination import WorkforceResourceCostCoordinator
    bad=WorkforceResourceCostCoordinator('ws',planner).evaluate(plan=plan,policy=ResourceCostPolicy(max_total_cost=0,max_total_resource_units=2),
                      estimates=(ResourceCostEstimate('a',1,1),))
    bad_cost=WorkforceResourceCostCoordinator('ws',planner)
    bad=bad_cost.evaluate(plan=plan,policy=ResourceCostPolicy(max_total_cost=0,max_total_resource_units=2), estimates=(ResourceCostEstimate('a',1,1),))
    rr=WorkforceExecutionReadinessRegistry('ws',planner,bad_cost,registry,allocation)
    with pytest.raises(ValueError,match=ReadinessBlock.COST_PLAN_INFEASIBLE.value):
        rr.evaluate(readiness_id='rd1',plan=plan,cost_plan=bad,reservation_ids={'a':reservation.reservation_id})


def test_reservation_capacity_is_checked_across_tasks(tmp_path):
    planner,_,cost,_,registry,allocation,reservation,r=setup(tmp_path)
    q=planner.qualification
    plan=planner.plan(plan_id='p2',objective_id='o2',cycle_id='c2',coordinator_worker_id='coord',resource_budget=2,
                      specs=(CoordinationTaskSpec('a','security','a'),CoordinationTaskSpec('b','security','b')))
    cp=cost.evaluate(plan=plan,policy=ResourceCostPolicy(max_total_cost=10,max_total_resource_units=4),
                     estimates=(ResourceCostEstimate('a',1,1),ResourceCostEstimate('b',1,1)))
    with pytest.raises(ValueError,match=ReadinessBlock.RESERVATION_INSUFFICIENT.value):
        r.evaluate(readiness_id='rd2',plan=plan,cost_plan=cp,reservation_ids={'a':reservation.reservation_id,'b':reservation.reservation_id})


def test_authority_tamper_is_fenced(tmp_path):
    _,plan,_,cp,_,_,reservation,r=setup(tmp_path)
    out=r.evaluate(readiness_id='rd1',plan=plan,cost_plan=cp,reservation_ids={'a':reservation.reservation_id})
    bad=WorkforceExecutionReadiness(**{**out.__dict__,'execution_requested':True})
    with pytest.raises(ValueError,match=ReadinessBlock.READINESS_TAMPERED.value):
        r.assert_integrity(bad)
