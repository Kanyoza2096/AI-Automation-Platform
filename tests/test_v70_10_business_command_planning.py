import pytest
from services.v70_10_business_command_planning import *

class FakeLayer:
    def __init__(self, ws): self.workspace_id=ws; self.calls=[]
    async def handle_event(self, objective_id, workflow_id, event, approved=False, now=None):
        self.calls.append((objective_id,workflow_id,event,approved))
        class A: reasons=("admitted",)
        class R: accepted=True; admission=A()
        return R()

def objective(ws='w1'):
    return BusinessObjective('obj1',ws,'Increase revenue',100000,'USD','high',30,(PlanningConstraint('max_cost',5000),))

def test_plan_prioritizes_and_respects_capacity():
    p=BusinessCommandPlanning('w1')
    r=p.create_plan(objective=objective(), capacity_budget=3, signals={'actions':[
        {'title':'Recover at-risk accounts','priority':'critical','effort':2,'expected_impact':5000},
        {'title':'Improve onboarding','priority':'high','effort':2,'expected_impact':3000},
        {'title':'Review pricing','priority':'medium','effort':1,'expected_impact':1000}]}, evidence=('crm.health','sales.pipeline'))
    assert r.accepted and len(r.plan.items)==2 and r.plan.capacity_used==3
    assert r.plan.items[0].title=='Recover at-risk accounts'
    assert r.plan.tradeoffs

def test_planning_never_invents_executable_authority():
    p=BusinessCommandPlanning('w1')
    with pytest.raises(ValueError,match='invalid_target'):
        p.create_plan(objective=objective(), capacity_budget=5, signals={'actions':[{'title':'Do thing','executable':True}]})
    r=p.create_plan(objective=objective(),capacity_budget=5,signals={'actions':[{'title':'Advise operator'}]})
    with pytest.raises(ValueError,match='advisory_only'):
        import asyncio; asyncio.run(p.command(plan_id=r.plan.plan_id,command_id='c1',item_id=r.plan.items[0].item_id))

def test_governed_command_delegates_only_explicit_workflow():
    layer=FakeLayer('w1'); p=BusinessCommandPlanning('w1',operating_layer=layer)
    r=p.create_plan(objective=objective(),capacity_budget=5,signals={'actions':[{
        'title':'Review plan','priority':'high','effort':1,'expected_impact':10,
        'governed_workflow_id':'management.plan-attention','executable':True}]})
    import asyncio
    out=asyncio.run(p.command(plan_id=r.plan.plan_id,command_id='c1',item_id=r.plan.items[0].item_id))
    assert out.execution is not None and layer.calls[0][1]=='management.plan-attention'
    with pytest.raises(ValueError,match='duplicate_command'):
        asyncio.run(p.command(plan_id=r.plan.plan_id,command_id='c1',item_id=r.plan.items[0].item_id))

def test_tenant_and_validation_fences():
    p=BusinessCommandPlanning('w1')
    with pytest.raises(ValueError,match='tenant_mismatch'):
        p.create_plan(objective=objective('w2'),capacity_budget=1,signals={'actions':[]})
    with pytest.raises(ValueError,match='invalid_capacity'):
        p.create_plan(objective=objective(),capacity_budget=-1,signals={'actions':[]})

def test_fingerprint_is_deterministic():
    p=BusinessCommandPlanning('w1')
    r=p.create_plan(objective=objective(),capacity_budget=2,signals={'actions':[{'title':'A','effort':1}]})
    assert r.plan.fingerprint()==r.plan.fingerprint()
