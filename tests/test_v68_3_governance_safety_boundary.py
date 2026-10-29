import pytest

from council.engine import DemocracyEngine
from council.models import DecisionState, DeliberationResult, Proposal, RiskLevel
from council.orchestration.council_orchestrator import CouncilOrchestrator
from council.policies.governance_safety import enforce_risk_decision_boundary


def _approved_result(risk=RiskLevel.CRITICAL):
    return DeliberationResult(
        proposal_id="p",
        decision=DecisionState.APPROVED,
        approve_ratio=1.0,
        reject_ratio=0.0,
        quorum_ratio=1.0,
        confidence=1.0,
        risk_level=risk,
    )


def test_final_boundary_converts_critical_approval_to_escalation():
    proposal = Proposal(action_name="delete_workspace", risk_level=RiskLevel.CRITICAL)
    result = enforce_risk_decision_boundary(proposal, _approved_result())
    assert result.decision == DecisionState.ESCALATED
    assert result.approve_ratio == 0.0
    assert any("human review" in w for w in result.warnings)


def test_medium_approval_is_unchanged():
    proposal = Proposal(action_name="crm_schedule_followup", risk_level=RiskLevel.MEDIUM)
    result = _approved_result(RiskLevel.MEDIUM)
    result = enforce_risk_decision_boundary(proposal, result)
    assert result.decision == DecisionState.APPROVED

@pytest.mark.asyncio
async def test_orchestrator_never_returns_approved_for_high_risk(monkeypatch):
    council = CouncilOrchestrator()
    proposal = Proposal(action_name="dangerous_action", risk_level=RiskLevel.HIGH, metadata={"workspace_id":"w"})

    async def fake_deep(*args, **kwargs):
        return _approved_result(RiskLevel.HIGH)
    monkeypatch.setattr(council.deep_deliberation, "deliberate", fake_deep)
    result = await council.deliberate(proposal)
    assert result.decision == DecisionState.ESCALATED

@pytest.mark.asyncio
async def test_democracy_engine_never_returns_approved_for_critical(monkeypatch):
    engine = DemocracyEngine()
    proposal = Proposal(action_name="delete_workspace", risk_level=RiskLevel.CRITICAL, metadata={"workspace_id":"w"})
    def fake_consensus(*args, **kwargs):
        return _approved_result(RiskLevel.CRITICAL)
    monkeypatch.setattr(engine.consensus, "deliberate", fake_consensus)
    result = await engine.deliberate(proposal)
    assert result.decision == DecisionState.ESCALATED
