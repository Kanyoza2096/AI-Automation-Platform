import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_9_worker_selection import WorkerSelectionRegistry, WorkerCapacityRegistry
from services.v71_21_worker_registry import WorkerRegistry, WorkerKind, WorkerLifecycle, WorkerHealth
from services.v71_24_workforce_capacity_allocation import WorkforceCapacityAllocation
from services.v71_25_worker_assignment import WorkerAssignmentAdmission
from services.v71_26_worker_assignment_lifecycle import WorkerAssignmentLifecycleRegistry
from services.v71_35_worker_capability_qualification import WorkerCapabilityQualificationRegistry
from services.v71_37_workforce_multi_worker_planning import CoordinationTaskSpec, WorkforceMultiWorkerPlanner
from services.v71_38_workforce_resource_cost_coordination import WorkforceResourceCostCoordinator, ResourceCostPolicy, ResourceCostEstimate
from services.v71_39_workforce_execution_readiness import WorkforceExecutionReadinessRegistry
from services.v71_40_coordinated_worker_assignment_admission import CoordinatedWorkerAssignmentAdmission
from services.v71_41_coordinated_worker_assignment_lifecycle import *
from tests.test_phase7_26_multi_worker_planning import FakeSelector, FakeRep


def setup2(tmp_path):
    ws='ws'; q=WorkerCapabilityQualificationRegistry(ws,FakeRep())
    q.certify(qualification_id='q-w1',worker_id='w1',capability='security',qualified_by='governance',issued_at='now')
    q.certify(qualification_id='q-w2',worker_id='w2',capability='supply',qualified_by='governance',issued_at='now')
    selector=FakeSelector(); planner=WorkforceMultiWorkerPlanner(ws,selector,q)
    plan=planner.plan(plan_id='p1',objective_id='o1',cycle_id='c1',coordinator_worker_id='coord',resource_budget=2,
        specs=(CoordinationTaskSpec('a','security','a'),CoordinationTaskSpec('b','supply','b',depends_on=('a',),candidate_worker_ids=('w2',))))
    cost=WorkforceResourceCostCoordinator(ws,planner)
    cp=cost.evaluate(plan=plan,policy=ResourceCostPolicy(max_total_cost=10,max_total_resource_units=4),
        estimates=(ResourceCostEstimate('a',1,1),ResourceCostEstimate('b',1,1)))
    identity=AgentIdentityRegistry(ws)
    for wid,cap in [('w1','security'),('w2','supply')]:
        identity.register(agent_id=wid,name=wid,purpose='work',declared_capabilities=(cap,)); identity.transition(agent_id=wid,status=AgentStatus.ACTIVE)
    capacity=WorkerCapacityRegistry(ws,identity)
    for wid in ('w1','w2'): capacity.register(agent_id=wid,max_concurrent_tasks=1)
    registry=WorkerRegistry(ws,identity,capacity)
    registry.create_pool(pool_id='pool1',name='Pool',worker_kind=WorkerKind.DETERMINISTIC,max_instances=2)
    for wid,cap in [('w1','security'),('w2','supply')]:
        registry.register(worker_id=wid,pool_id='pool1',name=wid,worker_kind=WorkerKind.DETERMINISTIC,declared_capabilities=(cap,)); registry.transition(worker_id=wid,lifecycle=WorkerLifecycle.READY); registry.transition(worker_id=wid,lifecycle=WorkerLifecycle.ACTIVE); registry.set_health(worker_id=wid,health=WorkerHealth.HEALTHY)
    allocation=WorkforceCapacityAllocation(ws,registry)
    from services.v71_23_workforce_demand_planning import WorkforceDemandPlan, DemandDisposition
    demand=WorkforceDemandPlan('d1',ws,'pool1',DemandDisposition.EXPAND,0,2,0,2,('reservation',),'policy','obs','forecast',True,False,False,'')
    demand=WorkforceDemandPlan(**{**demand.__dict__,'plan_fingerprint':demand.fingerprint()})
    reservation=allocation.allocate(reservation_id='r1',plan=demand,requested_slots=2)
    readiness=WorkforceExecutionReadinessRegistry(ws,planner,cost,registry,allocation)
    rr=readiness.evaluate(readiness_id='rd1',plan=plan,cost_plan=cp,reservation_ids={'a':reservation.reservation_id,'b':reservation.reservation_id})
    assignment=WorkerAssignmentAdmission(ws,registry,selector,allocation,str(tmp_path/'assign.db'))
    admission=CoordinatedWorkerAssignmentAdmission(ws,readiness,assignment,planner,str(tmp_path/'coord.db'))
    admission.admit(admission_id='adm1',readiness_id='rd1')
    single=WorkerAssignmentLifecycleRegistry(ws,assignment,capacity,str(tmp_path/'single.db'))
    coordinated=CoordinatedWorkerAssignmentLifecycle(ws,admission,single,str(tmp_path/'group.db'))
    return coordinated, capacity


def test_dependency_order_and_partial_lifecycle(tmp_path):
    c,cap=setup2(tmp_path); out=c.create(coordination_lifecycle_id='cl1',admission_id='adm1')
    assert out.state is CoordinatedLifecycleState.ASSIGNED
    with pytest.raises(ValueError, match=CoordinatedLifecycleBlock.DEPENDENCY_NOT_RELEASED.value): c.start_task(coordination_lifecycle_id='cl1',task_id='b')
    out=c.start_task(coordination_lifecycle_id='cl1',task_id='a')
    assert out.state is CoordinatedLifecycleState.ACTIVE and cap.get('w1').active_tasks==1
    out=c.release_task(coordination_lifecycle_id='cl1',task_id='a',reason='dependency_complete')
    assert out.state is CoordinatedLifecycleState.PARTIAL and cap.get('w1').active_tasks==0
    out=c.start_task(coordination_lifecycle_id='cl1',task_id='b')
    assert out.state is CoordinatedLifecycleState.ACTIVE
    out=c.release_task(coordination_lifecycle_id='cl1',task_id='b',reason='complete')
    assert out.state is CoordinatedLifecycleState.RELEASED and cap.get('w2').active_tasks==0


def test_cancellation_propagates_to_downstream_and_releases_capacity_once(tmp_path):
    c,cap=setup2(tmp_path); c.create(coordination_lifecycle_id='cl1',admission_id='adm1')
    c.start_task(coordination_lifecycle_id='cl1',task_id='a')
    out=c.cancel_task(coordination_lifecycle_id='cl1',task_id='a',reason='upstream_cancelled')
    assert {t.state for t in out.tasks} == {CoordinatedTaskState.CANCELLED}
    assert cap.get('w1').active_tasks==0
    with pytest.raises(ValueError, match=CoordinatedLifecycleBlock.TERMINAL.value): c.cancel_task(coordination_lifecycle_id='cl1',task_id='a',reason='again')


def test_assigned_cancellation_does_not_consume_or_release_capacity(tmp_path):
    c,cap=setup2(tmp_path); c.create(coordination_lifecycle_id='cl1',admission_id='adm1')
    out=c.cancel_task(coordination_lifecycle_id='cl1',task_id='a',reason='before_start')
    assert cap.get('w1').active_tasks==0
    assert c.context('cl1')['task_counts']['cancelled']==2


def test_authority_and_tamper_are_fenced(tmp_path):
    c,_=setup2(tmp_path); out=c.create(coordination_lifecycle_id='cl1',admission_id='adm1')
    c.assert_integrity(out)
    bad=CoordinatedAssignmentLifecycle(**{**out.__dict__,'execution_requested':True})
    with pytest.raises(ValueError, match=CoordinatedLifecycleBlock.AUTHORITY_VIOLATION.value): c.assert_integrity(bad)


def test_duplicate_group_and_terminal_release_are_rejected(tmp_path):
    c,_=setup2(tmp_path); c.create(coordination_lifecycle_id='cl1',admission_id='adm1')
    with pytest.raises(ValueError, match=CoordinatedLifecycleBlock.DUPLICATE.value): c.create(coordination_lifecycle_id='cl1',admission_id='adm1')
    c.start_task(coordination_lifecycle_id='cl1',task_id='a'); c.release_task(coordination_lifecycle_id='cl1',task_id='a',reason='done')
    with pytest.raises(ValueError, match=CoordinatedLifecycleBlock.INVALID_TRANSITION.value): c.release_task(coordination_lifecycle_id='cl1',task_id='a',reason='again')
