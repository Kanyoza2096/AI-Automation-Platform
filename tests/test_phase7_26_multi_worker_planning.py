import pytest
from types import SimpleNamespace
from services.v71_35_worker_capability_qualification import WorkerCapabilityQualificationRegistry
from services.v71_37_workforce_multi_worker_planning import *

class FakeRep:
    workspace_id='ws'
    def profile(self,*,worker_id,capability):
        return SimpleNamespace(sample_count=5,verified_sample_count=5,average_capability_score=.9,average_performance_score=.9,evidence_ids=tuple(f'e{i}' for i in range(5)),can_authorize=False,can_execute=False,can_call_provider=False)

class FakeSelector:
    workspace_id='ws'
    def __init__(self): self._s={}
    def select(self,**kw):
        wid=(kw.get('candidate_agent_ids') or ('w1',))[0]
        from services.v71_9_worker_selection import WorkerSelection, WorkerCandidate
        c=WorkerCandidate(wid,1,1,1,1,1,1,('selected',))
        s=WorkerSelection(kw['selection_id'],'ws',kw['task_id'],kw['capability'],kw.get('requested_scope') or {},wid,(c,),True,False,False,'')
        s=WorkerSelection(**{**s.__dict__,'selection_fingerprint':s.fingerprint()});self._s[s.selection_id]=s;return s
    def get(self,x): return self._s.get(x)
    def assert_integrity(self,s): return None

def planner():
    q=WorkerCapabilityQualificationRegistry('ws',FakeRep())
    for w,c in [('w1','security'),('w2','supply')]: q.certify(qualification_id=f'q-{w}',worker_id=w,capability=c,qualified_by='governance',issued_at='now')
    return WorkforceMultiWorkerPlanner('ws',FakeSelector(),q)

def test_multi_worker_plan_is_dependency_aware_and_durable():
    p=planner().plan(plan_id='p1',objective_id='o1',cycle_id='c1',coordinator_worker_id='coord',resource_budget=2,specs=(
        CoordinationTaskSpec('a','security','contain'),CoordinationTaskSpec('b','supply','dispatch',depends_on=('a',),candidate_worker_ids=('w2',))))
    assert p.state is PlanState.READY and len(p.tasks)==2

def test_dependency_cycle_rejected():
    with pytest.raises(ValueError,match=PlanningBlock.DEPENDENCY_CYCLE.value):
        planner().plan(plan_id='p1',objective_id='o1',cycle_id='c1',coordinator_worker_id='coord',resource_budget=2,specs=(CoordinationTaskSpec('a','security','a',depends_on=('b',)),CoordinationTaskSpec('b','security','b',depends_on=('a',))))

def test_resource_overcommit_rejected():
    with pytest.raises(ValueError,match=PlanningBlock.RESOURCE_OVERCOMMIT.value):
        planner().plan(plan_id='p1',objective_id='o1',cycle_id='c1',coordinator_worker_id='coord',resource_budget=1,specs=(CoordinationTaskSpec('a','security','a'),CoordinationTaskSpec('b','security','b')))

def test_authority_and_workspace_are_fenced():
    pl=planner(); p=pl.plan(plan_id='p1',objective_id='o1',cycle_id='c1',coordinator_worker_id='coord',resource_budget=1,specs=(CoordinationTaskSpec('a','security','a'),))
    assert pl.context('p1')['authority']=='none'
    bad=MultiWorkerPlan(**{**p.__dict__,'authority_granted':True})
    with pytest.raises(ValueError,match=PlanningBlock.PLAN_TAMPERED.value): pl.assert_integrity(bad)
