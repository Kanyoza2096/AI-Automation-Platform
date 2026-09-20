from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from services.business_autonomy_envelope import (
    AutonomyMode, BusinessAutonomyEnvelope, EnvelopeState, RiskBoundary, SuccessMetric,
)
from services.business_workflow_autonomy import (
    WorkflowAdmission, evaluate_workflow_admission, run_catalog_workflow,
    assert_no_parallel_execution_path,
)


def envelope(mode=AutonomyMode.EXECUTE, actions=("crm_schedule_followup",), max_risk="medium"):
    return BusinessAutonomyEnvelope(
        workspace_id="ws-1",
        objective="Keep qualified leads moving",
        success_metrics=(SuccessMetric("follow_up_rate", ">=", 0.8),),
        allowed_actions=actions,
        scope={"workflow_ids": ["sales.follow-up"]},
        risk_boundary=RiskBoundary(max_risk=max_risk),
        mode=mode,
        state=EnvelopeState.ACTIVE,
        valid_from=datetime.now(timezone.utc) - timedelta(minutes=1),
    )


def workflow(risk="medium"):
    return {
        "id": "sales.follow-up",
        "trigger": "lead.follow_up",
        "action": "crm_schedule_followup",
        "risk": risk,
    }


def test_execute_mode_admits_bounded_workflow():
    decision = evaluate_workflow_admission(envelope(), workflow(), workspace_id="ws-1")
    assert decision.admission == WorkflowAdmission.ADMITTED
    assert decision.allowed_to_invoke_engine
    assert decision.authority == "none"


def test_observe_and_suggest_never_admit_execution():
    for mode, expected in ((AutonomyMode.OBSERVE, WorkflowAdmission.OBSERVED), (AutonomyMode.SUGGEST, WorkflowAdmission.SUGGESTED)):
        decision = evaluate_workflow_admission(envelope(mode), workflow(), workspace_id="ws-1")
        assert decision.admission == expected
        assert not decision.allowed_to_invoke_engine


def test_high_risk_never_becomes_auto_approved():
    decision = evaluate_workflow_admission(envelope(actions=("crm_add_deal",), max_risk="critical"), workflow("high"), workspace_id="ws-1")
    assert decision.admission == WorkflowAdmission.BLOCKED  # action itself is undeclared

    env = envelope(actions=("crm_schedule_followup",), max_risk="critical")
    high = dict(workflow(), risk="high")
    decision = evaluate_workflow_admission(env, high, workspace_id="ws-1")
    assert decision.admission == WorkflowAdmission.REVIEW_REQUIRED


def test_workspace_and_action_scope_fail_closed():
    decision = evaluate_workflow_admission(envelope(), workflow(), workspace_id="ws-other")
    assert decision.admission == WorkflowAdmission.BLOCKED
    assert "workspace_mismatch" in decision.reasons

    undeclared = dict(workflow(), action="crm_add_deal")
    decision = evaluate_workflow_admission(envelope(), undeclared, workspace_id="ws-1")
    assert decision.admission == WorkflowAdmission.BLOCKED
    assert "action_not_declared" in decision.reasons


@pytest.mark.asyncio
async def test_admitted_workflow_is_delegated_to_existing_engine_only():
    engine = AsyncMock()
    engine.run_event.return_value = ["existing-governed-result"]
    decision, result = await run_catalog_workflow(
        envelope(), workflow(), engine=engine,
        event={"event_id": "evt-1", "contact": "C1", "date": "2026-09-14"},
        workspace_id="ws-1",
    )
    assert decision.admission == WorkflowAdmission.ADMITTED
    assert result == ["existing-governed-result"]
    engine.run_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_blocked_workflow_never_reaches_engine():
    engine = AsyncMock()
    decision, result = await run_catalog_workflow(
        envelope(mode=AutonomyMode.SUGGEST), workflow(), engine=engine,
        event={"event_id": "evt-1"}, workspace_id="ws-1",
    )
    assert decision.admission == WorkflowAdmission.SUGGESTED
    assert result is None
    engine.run_event.assert_not_awaited()


def test_parallel_execution_metadata_is_rejected():
    with pytest.raises(ValueError):
        assert_no_parallel_execution_path({"provider_execute": True})
