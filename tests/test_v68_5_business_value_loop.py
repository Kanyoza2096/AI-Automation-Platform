from datetime import datetime, timedelta, timezone

import pytest

from services.business_autonomy_envelope import BusinessAutonomyEnvelope, SuccessMetric, AutonomyMode, EnvelopeState
from services.v68_business_value_loop import evaluate_business_value_loop, assert_value_loop_is_advisory


def envelope():
    now = datetime.now(timezone.utc)
    return BusinessAutonomyEnvelope(
        workspace_id="ws_v68_5",
        objective="Increase qualified follow-up completion",
        success_metrics=(SuccessMetric("followup_completion_rate", ">=", 0.8, minimum_confidence=0.7),),
        allowed_actions=("crm_schedule_followup",),
        mode=AutonomyMode.EXECUTE,
        state=EnvelopeState.ACTIVE,
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(hours=1),
    )


def test_successful_business_loop_can_continue():
    result = evaluate_business_value_loop(
        envelope(), workspace_id="ws_v68_5", outcome_state="succeeded",
        metrics={"followup_completion_rate": 0.91},
        next_action="crm_schedule_followup", learning_evaluation_state="effective",
        evidence_confidence=0.9,
    )
    assert result.disposition == "continue"
    assert result.next_action == "crm_schedule_followup"
    assert result.authority == "none"


def test_execution_success_without_business_metric_does_not_claim_success():
    result = evaluate_business_value_loop(
        envelope(), workspace_id="ws_v68_5", outcome_state="succeeded",
        metrics={}, evidence_confidence=0.95,
    )
    assert result.disposition == "improve_or_escalate"
    assert any(x.startswith("metric_missing") for x in result.reasons)


def test_uncertain_outcome_requires_review_and_no_next_action():
    result = evaluate_business_value_loop(
        envelope(), workspace_id="ws_v68_5", outcome_state="uncertain",
        metrics={"followup_completion_rate": 0.95},
        next_action="crm_schedule_followup", evidence_confidence=0.95,
    )
    assert result.disposition == "review_required"
    assert result.next_action is None


def test_low_confidence_blocks_metric_claim():
    result = evaluate_business_value_loop(
        envelope(), workspace_id="ws_v68_5", outcome_state="succeeded",
        metrics={"followup_completion_rate": 0.95},
        evidence_confidence=0.5,
    )
    assert result.disposition == "improve_or_escalate"
    assert any("confidence_insufficient" in x for x in result.reasons)


def test_learning_uncertainty_does_not_grant_continuation_authority():
    result = evaluate_business_value_loop(
        envelope(), workspace_id="ws_v68_5", outcome_state="succeeded",
        metrics={"followup_completion_rate": 0.95},
        learning_evaluation_state="mixed", evidence_confidence=0.95,
        next_action="crm_schedule_followup",
    )
    assert result.disposition == "continue_with_observation"
    assert result.next_action == "crm_schedule_followup"
    assert result.authority == "none"


def test_workspace_mismatch_fails_closed():
    result = evaluate_business_value_loop(
        envelope(), workspace_id="other", outcome_state="succeeded",
        metrics={"followup_completion_rate": 0.95}, evidence_confidence=0.95,
    )
    assert result.disposition == "blocked"
    assert "workspace_mismatch" in result.reasons


def test_inactive_envelope_fails_closed():
    env = envelope()
    env = BusinessAutonomyEnvelope(
        workspace_id=env.workspace_id, objective=env.objective,
        success_metrics=env.success_metrics, allowed_actions=env.allowed_actions,
        mode=env.mode, state=EnvelopeState.SUSPENDED,
        valid_from=env.valid_from, valid_until=env.valid_until,
    )
    result = evaluate_business_value_loop(
        env, workspace_id=env.workspace_id, outcome_state="succeeded",
        metrics={"followup_completion_rate": 0.95}, evidence_confidence=0.95,
    )
    assert result.disposition == "blocked"


def test_advisory_boundary_rejects_execution_metadata():
    assert_value_loop_is_advisory({"authority": "none"})
    with pytest.raises(ValueError):
        assert_value_loop_is_advisory({"authority": "execute"})
    with pytest.raises(ValueError):
        assert_value_loop_is_advisory({"provider_execute": True})
