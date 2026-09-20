import asyncio
import pytest
from services.v70_11_cross_domain_business_orchestration import *

class FakeRun:
    accepted=True
class FakeLayer:
    def __init__(self, ws, workflows):
        self.workspace_id=ws; self.calls=[]
        class Obj: pass
        self.objectives={'o1': Obj()}; self.objectives['o1'].workflow_ids=tuple(workflows)
    async def handle_event(self, objective_id, workflow_id, event, approved=False, now=None):
        self.calls.append((workflow_id,event,approved)); return FakeRun()

def layer_for(s): return FakeLayer('w1', SCENARIOS[s])

def test_prepare_is_explicit_and_deterministic():
    l=layer_for('customer.retention-risk'); o=CrossDomainBusinessOrchestrator('w1',operating_layer=l)
    r=o.prepare(scenario='customer.retention-risk',event={'event_id':'e1','workspace_id':'w1','customer_id':'c1'},objective_id='o1')
    assert r.accepted and len(r.steps)==2 and r.state.fingerprint()==r.state.fingerprint()

def test_execute_chains_only_catalog_workflows():
    s='customer.retention-risk'; l=layer_for(s); o=CrossDomainBusinessOrchestrator('w1',operating_layer=l)
    r=o.prepare(scenario=s,event={'event_id':'e1','customer_id':'c1'},objective_id='o1')
    out=asyncio.run(o.execute(orchestration_id=r.state.orchestration_id,event={'event_id':'e1','customer_id':'c1'}))
    assert out.accepted and out.state.status==OrchestrationStatus.COMPLETED
    assert [x[0] for x in l.calls]==list(SCENARIOS[s])

def test_scope_and_tenant_fences():
    l=layer_for('customer.retention-risk'); o=CrossDomainBusinessOrchestrator('w1',operating_layer=l)
    with pytest.raises(ValueError,match='tenant_mismatch'):
        o.prepare(scenario='customer.retention-risk',event={'event_id':'e','workspace_id':'w2'},objective_id='o1')
    l.objectives['o1'].workflow_ids=(SCENARIOS['customer.retention-risk'][0],)
    with pytest.raises(ValueError,match='workflow_outside_objective_scope'):
        o.prepare(scenario='customer.retention-risk',event={'event_id':'e2'},objective_id='o1')

def test_duplicate_event_and_terminal_fences():
    s='business.plan.risk'; l=layer_for(s); o=CrossDomainBusinessOrchestrator('w1',operating_layer=l)
    r=o.prepare(scenario=s,event={'event_id':'e1'},objective_id='o1')
    asyncio.run(o.execute(orchestration_id=r.state.orchestration_id,event={'event_id':'e1'}))
    with pytest.raises(ValueError,match='terminal'):
        asyncio.run(o.execute(orchestration_id=r.state.orchestration_id,event={'event_id':'e1'}))
    with pytest.raises(ValueError,match='duplicate_event'):
        o.prepare(scenario=s,event={'event_id':'e1'},objective_id='o1')
