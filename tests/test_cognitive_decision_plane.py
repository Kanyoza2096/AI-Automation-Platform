from datetime import datetime, timezone, timedelta

import pytest

from core.decision_gate import DecisionGate
from core.organism_state import OrganismState
from organism.cognitive import CognitiveDecisionPlane
from organism.goals import Goal, GoalPriority
from council.models import DecisionState


class ApprovingCouncil:
    async def deliberate(self, proposal):
        from council.models import DeliberationResult, RiskLevel
        return DeliberationResult(
            proposal_id=proposal.id,
            decision=DecisionState.APPROVED,
            approve_ratio=1.0,
            reject_ratio=0.0,
            quorum_ratio=1.0,
            confidence=0.91,
            risk_level=proposal.risk_level,
            tier_used=2,
        )


class RejectingCouncil(ApprovingCouncil):
    async def deliberate(self, proposal):
        result = await super().deliberate(proposal)
        result.decision = DecisionState.REJECTED
        result.confidence = 0.88
        return result


def state(**kwargs):
    return OrganismState(workspace_id="ws-1", **kwargs)


def goal(authority="execute"):
    return Goal(
        workspace_id="ws-1",
        title="Grow engagement",
        success_condition="engagement improves",
        priority=GoalPriority.HIGH,
        authority=authority,
        deadline=datetime.now(timezone.utc) + timedelta(days=1),
    )


@pytest.mark.asyncio
async def test_approved_action_gets_scoped_decision_gate_id():
    gate = DecisionGate()
    plane = CognitiveDecisionPlane(council=ApprovingCouncil(), decision_gate=gate)
    result = await plane.evaluate(state=state(), goal=goal(), action_name="schedule_content")
    assert result.executable is True
    assert result.decision_id
    decision = gate.get(result.decision_id)
    assert decision.workspace_id == "ws-1"
    assert decision.action_name == "schedule_content"


@pytest.mark.asyncio
async def test_suggest_goal_cannot_authorize_execution():
    plane = CognitiveDecisionPlane(council=ApprovingCouncil())
    result = await plane.evaluate(state=state(), goal=goal("suggest"), action_name="schedule_content")
    assert result.decision == DecisionState.BLOCKED
    assert result.blocked is True
    assert result.decision_id is None


@pytest.mark.asyncio
async def test_forbidden_action_is_blocked_before_council():
    class ExplodingCouncil:
        async def deliberate(self, proposal):
            raise AssertionError("Council must not receive forbidden actions")
    plane = CognitiveDecisionPlane(council=ExplodingCouncil())
    result = await plane.evaluate(state=state(), goal=goal(), action_name="disable_security_controls")
    assert result.decision == DecisionState.BLOCKED
    assert "forbidden" in result.reason


@pytest.mark.asyncio
async def test_quarantined_state_cannot_execute():
    plane = CognitiveDecisionPlane(council=ApprovingCouncil())
    result = await plane.evaluate(
        state=state(security_posture=0.2), goal=goal(), action_name="schedule_content"
    )
    assert result.decision == DecisionState.BLOCKED
    assert result.decision_id is None


@pytest.mark.asyncio
async def test_human_required_action_escalates_without_gate_authorization():
    gate = DecisionGate()
    plane = CognitiveDecisionPlane(council=ApprovingCouncil(), decision_gate=gate)
    result = await plane.evaluate(state=state(), goal=goal(), action_name="change_billing")
    assert result.decision == DecisionState.ESCALATED
    assert result.requires_human_approval is True
    assert result.decision_id is None


@pytest.mark.asyncio
async def test_council_rejection_never_issues_gate_decision():
    gate = DecisionGate()
    plane = CognitiveDecisionPlane(council=RejectingCouncil(), decision_gate=gate)
    result = await plane.evaluate(state=state(), goal=goal(), action_name="schedule_content")
    assert result.decision == DecisionState.REJECTED
    assert result.decision_id is None
