import pytest
from services.v70_7_sales_pipeline_opportunity_operating_system import *
from services.v70_business_operating_layer import BusinessOperatingLayer

class FakeLayer:
    def __init__(self,ws): self.workspace_id=ws
    async def handle_event(self, objective_id, workflow_id, event, approved=False, now=None):
        class A: reasons=[]
        class R: accepted=True; admission=A()
        return R()

@pytest.mark.asyncio
async def test_lifecycle_and_metrics():
    loop=SalesPipelineOperatingSystem('w1', operating_layer=FakeLayer('w1'))
    r=await loop.process(event={'event_id':'e1','event_name':'opportunity.created','opportunity_id':'o1','amount':1000,'contact':'A'},objective_id='obj')
    assert r.stage==OpportunityStage.QUALIFIED
    await loop.process(event={'event_id':'e2','event_name':'opportunity.discovery_started','opportunity_id':'o1'},objective_id='obj')
    await loop.process(event={'event_id':'e3','event_name':'opportunity.proposal_sent','opportunity_id':'o1'},objective_id='obj')
    m=loop.metrics(); assert m.open_value==1000 and m.weighted_pipeline==600

@pytest.mark.asyncio
async def test_tenant_and_duplicate_fences():
    loop=SalesPipelineOperatingSystem('w1', operating_layer=FakeLayer('w1'))
    with pytest.raises(ValueError, match='tenant_mismatch'):
        await loop.process(event={'event_id':'e1','event_name':'opportunity.created','opportunity_id':'o1','workspace_id':'w2'},objective_id='obj')
    await loop.process(event={'event_id':'e1','event_name':'opportunity.created','opportunity_id':'o1','amount':1},objective_id='obj')
    with pytest.raises(ValueError, match='duplicate_event'):
        await loop.process(event={'event_id':'e1','event_name':'opportunity.created','opportunity_id':'o1','amount':1},objective_id='obj')

@pytest.mark.asyncio
async def test_terminal_fence():
    loop=SalesPipelineOperatingSystem('w1', operating_layer=FakeLayer('w1'))
    await loop.process(event={'event_id':'e1','event_name':'opportunity.created','opportunity_id':'o1','amount':1},objective_id='obj')
    await loop.process(event={'event_id':'e2','event_name':'opportunity.won','opportunity_id':'o1'},objective_id='obj')
    with pytest.raises(ValueError, match='terminal'):
        await loop.process(event={'event_id':'e3','event_name':'opportunity.discovery_started','opportunity_id':'o1'},objective_id='obj')
