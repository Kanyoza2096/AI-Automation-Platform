import pytest

from council.intelligence.decision_router import DecisionRouter
from council.models import DecisionState, Proposal, RiskLevel
from council.orchestration.council_orchestrator import CouncilOrchestrator


def test_destructive_actions_cannot_be_routed_to_auto_approve():
    router = DecisionRouter()
    proposal = Proposal(action_name="delete_workspace", risk_level=RiskLevel.TRIVIAL)
    tier = router.route(proposal)
    assert tier == 3
    assert proposal.risk_level == RiskLevel.CRITICAL
    names = router.select_agents(proposal, tier)
    assert "risk_manager" in names

@pytest.mark.asyncio
async def test_irreversible_action_is_blocked_by_risk_veto():
    council = CouncilOrchestrator()
    proposal = Proposal(
        action_name="delete_workspace",
        proposal_type="general",
        risk_level=RiskLevel.CRITICAL,
        metadata={"workspace_id": "w1"},
    )
    result = await council.deliberate(proposal)
    assert result.decision == DecisionState.BLOCKED
    assert any(v.member == "risk_manager" and v.veto_type.value == "hard" for v in result.votes)

@pytest.mark.asyncio
async def test_critical_action_without_safety_plan_cannot_be_approved():
    council = CouncilOrchestrator()
    proposal = Proposal(
        action_name="change_production_secrets",
        proposal_type="general",
        risk_level=RiskLevel.CRITICAL,
        metadata={"workspace_id": "w1"},
    )
    result = await council.deliberate(proposal)
    assert result.decision in {DecisionState.ESCALATED, DecisionState.NEEDS_REVISION, DecisionState.BLOCKED}
    assert result.decision != DecisionState.APPROVED
