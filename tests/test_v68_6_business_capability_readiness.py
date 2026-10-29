from datetime import datetime, timedelta, timezone

import pytest

from services.business_autonomy_envelope import BusinessAutonomyEnvelope, SuccessMetric, AutonomyMode, EnvelopeState, RiskBoundary
from services.v68_business_capability_readiness import (
    Readiness,
    assert_readiness_is_advisory,
    evaluate_business_capability_readiness,
)


def env(actions=("crm_add_contact",), max_risk="medium"):
    now = datetime.now(timezone.utc)
    return BusinessAutonomyEnvelope(
        workspace_id="ws_v68_6",
        objective="Improve lead capture",
        success_metrics=(SuccessMetric("lead_capture_rate", ">=", 0.8),),
        allowed_actions=actions,
        risk_boundary=RiskBoundary(max_risk=max_risk),
        mode=AutonomyMode.EXECUTE,
        state=EnvelopeState.ACTIVE,
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(hours=1),
    )


def ready_caps():
    return {
        "crm": "ready",
        "lead_created_events": "ready",
    }


def test_catalog_workflow_is_ready_when_runtime_capabilities_are_ready():
    result = evaluate_business_capability_readiness(
        env(), workspace_id="ws_v68_6", workflow_id="sales.new-lead-capture",
        capabilities=ready_caps(),
    )
    assert result.readiness == Readiness.READY
    assert result.admission == "admitted"
    assert result.authority == "none"


def test_missing_real_capability_blocks_even_when_envelope_allows_action():
    result = evaluate_business_capability_readiness(
        env(), workspace_id="ws_v68_6", workflow_id="sales.new-lead-capture",
        capabilities={"lead_created_events": "ready"},
    )
    assert result.readiness == Readiness.BLOCKED
    assert "capability_missing:crm" in result.reasons


def test_unknown_capability_is_not_optimistically_treated_as_ready():
    result = evaluate_business_capability_readiness(
        env(), workspace_id="ws_v68_6", workflow_id="sales.new-lead-capture",
        capabilities={"crm": "unknown", "lead_created_events": "ready"},
    )
    assert result.readiness == Readiness.UNKNOWN
    assert "capability_unknown:crm" in result.reasons


def test_high_risk_workflow_is_review_required_not_auto_approved():
    result = evaluate_business_capability_readiness(
        env(actions=("crm_add_deal",), max_risk="high"), workspace_id="ws_v68_6",
        workflow_id="sales.qualified-lead-deal",
        capabilities={"crm": "ready", "lead_qualified_events": "ready"},
    )
    assert result.readiness == Readiness.REVIEW_REQUIRED
    assert result.human_review_required is True


def test_workspace_mismatch_blocks():
    result = evaluate_business_capability_readiness(
        env(), workspace_id="other", workflow_id="sales.new-lead-capture",
        capabilities=ready_caps(),
    )
    assert result.readiness == Readiness.BLOCKED
    assert "workspace_mismatch" in result.reasons


def test_unknown_workflow_blocks():
    result = evaluate_business_capability_readiness(
        env(), workspace_id="ws_v68_6", workflow_id="not.real",
        capabilities=ready_caps(),
    )
    assert result.readiness == Readiness.BLOCKED
    assert "workflow_not_found" in result.reasons


def test_readiness_cannot_become_execution_path():
    assert_readiness_is_advisory({"authority": "none", "execution_effect": "none"})
    with pytest.raises(ValueError):
        assert_readiness_is_advisory({"auto_approve": True})
    with pytest.raises(ValueError):
        assert_readiness_is_advisory({"provider_execute": True})
