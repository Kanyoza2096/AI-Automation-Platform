import pytest
from services.v71_35_worker_capability_qualification import WorkerCapabilityQualificationRegistry
from services.v71_37_workforce_multi_worker_planning import CoordinationTaskSpec, WorkforceMultiWorkerPlanner
from services.v71_38_workforce_resource_cost_coordination import *
from tests.test_phase7_26_multi_worker_planning import FakeSelector

def setup():
    q=WorkerCapabilityQualificationRegistry('ws', __import__('tests.test_phase7_26_multi_worker_planning',fromlist=['FakeRep']).FakeRep())
    q.certify(qualification_id='q-w1',worker_id='w1',capability='security',qualified_by='governance',issued_at='now')
    p=WorkforceMultiWorkerPlanner('ws',FakeSelector(),q)
    plan=p.plan(plan_id='p1',objective_id='o1',cycle_id='c1',coordinator_worker_id='coord',resource_budget=2,specs=(CoordinationTaskSpec('a','security','contain'),))
    return p,plan

def test_cost_and_resource_feasibility_is_advisory():
    p,base=setup(); c=WorkforceResourceCostCoordinator('ws',p)
    r=c.evaluate(plan=base,policy=ResourceCostPolicy(max_total_cost=10,max_total_resource_units=2),estimates=(ResourceCostEstimate('a',estimated_cost=3,resource_units=1),))
    assert r.feasible and r.total_cost==3 and c.context('p1')['authority']=='none'

def test_over_budget_is_not_executed_or_replanned():
    p,base=setup(); c=WorkforceResourceCostCoordinator('ws',p)
    r=c.evaluate(plan=base,policy=ResourceCostPolicy(max_total_cost=2,max_total_resource_units=2),estimates=(ResourceCostEstimate('a',estimated_cost=3),))
    assert not r.feasible and r.execution_requested is False

def test_task_cost_ceiling_rejects_invalid_estimate():
    p,base=setup(); c=WorkforceResourceCostCoordinator('ws',p)
    with pytest.raises(ValueError,match=ResourceCostBlock.TASK_COST_EXCEEDED.value):
        c.evaluate(plan=base,policy=ResourceCostPolicy(max_total_cost=10,max_total_resource_units=2,max_task_cost=2),estimates=(ResourceCostEstimate('a',estimated_cost=3),))

def test_tamper_and_authority_fence():
    p,base=setup(); c=WorkforceResourceCostCoordinator('ws',p)
    r=c.evaluate(plan=base,policy=ResourceCostPolicy(max_total_cost=10,max_total_resource_units=2),estimates=(ResourceCostEstimate('a',1),))
    bad=ResourceCostPlan(**{**r.__dict__,'authority_granted':True})
    with pytest.raises(ValueError,match=ResourceCostBlock.PLAN_TAMPERED.value): c.assert_integrity(bad)
