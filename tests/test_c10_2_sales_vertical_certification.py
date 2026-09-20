import asyncio
from pathlib import Path
from services.c10_2_sales_vertical_certification import SalesC102Status, certify_sales_repository
from services.v70_2_customer_sales_operating_loop import CustomerSalesOperatingLoop, LeadState

ROOT = Path(__file__).resolve().parents[1]

def test_sales_vertical_certification_has_no_blockers():
    cert = certify_sales_repository(ROOT)
    assert cert.status == SalesC102Status.REVIEW
    assert all(c.status != SalesC102Status.BLOCKED for c in cert.checks)

class FakeAdmission:
    reasons = ()
class FakeRun:
    accepted = True
    admission = FakeAdmission()
    run_id = "run-test"
class FakeOperatingLayer:
    workspace_id = "ws1"
    def __init__(self): self.calls=[]
    async def handle_event(self, objective_id, workflow_id, *, event, approved=False, now=None):
        self.calls.append((objective_id, workflow_id, dict(event)))
        return FakeRun()

def test_sales_loop_remains_a_state_and_routing_layer():
    layer = FakeOperatingLayer()
    loop = CustomerSalesOperatingLoop("ws1", operating_layer=layer)
    result = asyncio.run(loop.process(event={"event_id":"e1", "event_name":"lead.created", "lead_id":"l1"}, objective_id="obj1"))
    assert result.state == LeadState.NEW
    assert result.accepted is True
    assert len(layer.calls) == 1
    assert not hasattr(loop, "action_kernel")
    assert not hasattr(loop, "provider")
