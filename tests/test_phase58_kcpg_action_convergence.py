import pytest

from services.action_kernel import build_default_action_kernel


def test_kcpg_side_effect_contracts_are_registered():
    kernel = build_default_action_kernel(db=None, allow_in_memory=True)
    for action in (
        "affiliate_publish_promotion",
        "affiliate_pause_product",
        "affiliate_optimize_campaign",
        "publish_content",
    ):
        assert action in kernel._contracts


@pytest.mark.asyncio
async def test_high_risk_kcpg_affiliate_action_is_durable_approval_gated():
    kernel = build_default_action_kernel(db=None, allow_in_memory=True)
    result = await kernel.enqueue(
        workspace_id="ws-kcpg",
        action_type="affiliate_pause_product",
        parameters={"product_id": "p1"},
        risk_level="high",
        decision_id="decision-kcpg-1",
        idempotency_key="kcpg-test-1",
        source="kcpg:affiliate.pause_product",
    )
    assert result["status"] == "approval_required"
    assert result["accepted"] is False


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
