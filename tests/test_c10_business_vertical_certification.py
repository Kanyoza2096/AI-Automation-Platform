from pathlib import Path
import asyncio
import pytest

from services.c10_business_vertical_certification import C10Status, certify_repository
from services.v70_6_content_marketing_operating_loop import ContentMarketingOperatingLoop, MarketingObjective, ContentBrief

ROOT = Path(__file__).resolve().parents[1]

def test_c10_repository_vertical_convergence():
    cert = certify_repository(ROOT)
    names = {c.name: c for c in cert.checks}
    assert cert.status == C10Status.REVIEW
    assert all(c.status != C10Status.BLOCKED for c in cert.checks)
    assert names["marketing_content_to_action_kernel"].status == C10Status.PASS
    assert names["sales_to_action_kernel"].status == C10Status.PASS
    assert names["autonomous_publish_workforce_route"].status == C10Status.PASS
    assert names["scheduled_post_outbox_identity"].status == C10Status.PASS
    assert names["destructive_council_floor"].status == C10Status.PASS

class FakeKernel:
    def __init__(self): self.calls=[]
    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "status": "queued", "execution_id": "ex1"}

def test_content_vertical_delegates_once_to_action_kernel():
    loop=ContentMarketingOperatingLoop(action_kernel=FakeKernel())
    loop.register_objective(MarketingObjective("o1","ws1","Demand","leads",channels=("facebook",)))
    loop.create_brief(ContentBrief("b1","ws1","o1","topic",channels=("facebook",)))
    c=loop.create_candidate("b1",caption="draft")
    result=asyncio.run(loop.execute_publish(c.candidate_id, platform="facebook", decision_id="d1", approved=True))
    assert result["status"] == "queued"
    assert loop.get_candidate(c.candidate_id).lifecycle.value == "governance_pending"
    assert len(loop._action_kernel.calls) == 1
    assert loop._action_kernel.calls[0]["action_type"] == "publish_content"

def test_content_publish_requires_kernel():
    loop=ContentMarketingOperatingLoop()
    loop.register_objective(MarketingObjective("o1","ws1","Demand","leads",channels=("facebook",)))
    loop.create_brief(ContentBrief("b1","ws1","o1","topic",channels=("facebook",)))
    c=loop.create_candidate("b1",caption="draft")
    with pytest.raises(RuntimeError, match="governed Action Kernel"):
        asyncio.run(loop.execute_publish(c.candidate_id, platform="facebook", decision_id="d1"))
