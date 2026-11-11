import pytest

from services.action_kernel import build_default_action_kernel


@pytest.mark.asyncio
async def test_publish_action_is_approval_and_decision_gated():
    kernel = build_default_action_kernel(db=None, allow_in_memory=True)
    result = await kernel.enqueue(
        workspace_id="ws-publish",
        action_type="publish_content",
        parameters={"platform": "facebook", "caption": "hello", "decision_id": "decision-1"},
        risk_level="high",
        decision_id="decision-1",
        idempotency_key="publish-kcpg-1",
        source="kcpg:publish.execute",
    )
    assert result["status"] == "approval_required"
    assert result["accepted"] is False
