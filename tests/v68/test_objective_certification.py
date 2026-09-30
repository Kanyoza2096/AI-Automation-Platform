from datetime import datetime, timedelta, timezone

from services.business_autonomy_envelope import (
    AutonomyMode, BusinessAutonomyEnvelope, EnvelopeState, ResourceLimit,
    RiskBoundary, SuccessMetric,
)
from services.v68_objective_certification import certify_business_objective, certification_fingerprint


def make_envelope():
    now = datetime.now(timezone.utc)
    return BusinessAutonomyEnvelope(
        workspace_id="ws-v68",
        objective="Increase qualified sales conversations safely.",
        success_metrics=(SuccessMetric("qualified_conversations", ">=", 10, minimum_confidence=0.8),),
        allowed_actions=("crm_schedule_followup",),
        scope={"brand_id": "brand-v68"},
        risk_boundary=RiskBoundary(max_risk="medium"),
        resource_limits=(ResourceLimit("followups_per_day", 20, "day"),),
        mode=AutonomyMode.EXECUTE,
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(hours=1),
        state=EnvelopeState.ACTIVE,
    )


def test_end_to_end_objective_certification_crosses_existing_planes():
    result = certify_business_objective(make_envelope())
    assert result.certified is True
    assert all(result.stage_results.values())
    assert result.evidence["provider_invocations"] == 1
    assert result.evidence["business_success"] is True
    assert result.evidence["attribution_status"] == "attributed"
    assert result.evidence["learning_advisory_only"] is True


def test_certification_preserves_single_lineage():
    result = certify_business_objective(make_envelope())
    assert result.lineage["envelope_id"]
    assert result.lineage["understanding_id"]
    assert result.lineage["candidate_id"]
    assert result.lineage["evaluation_id"]
    assert result.lineage["handoff_id"]
    assert result.lineage["intent_id"]
    assert result.lineage["execution_id"]
    assert result.lineage["business_observation_id"]
    assert result.lineage["attribution_id"]
    assert certification_fingerprint(result) == certification_fingerprint(result)


def test_envelope_cannot_expand_action_authority():
    env = make_envelope()
    result = certify_business_objective(env)
    assert result.certified
    assert env.authority == "none"
    assert env.execution_effect == "none"
    assert env.approval_effect == "none"
    assert env.policy_effect == "none"


def test_undeclared_objective_action_fails_closed():
    env = make_envelope()
    env = BusinessAutonomyEnvelope(**{**env.__dict__, "allowed_actions": ("notify_operator",)})
    result = certify_business_objective(env)
    assert result.certified is False
    assert "objective envelope" in " ".join(result.failures)
