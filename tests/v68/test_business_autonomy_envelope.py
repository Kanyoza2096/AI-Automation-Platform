from datetime import datetime, timedelta, timezone

import pytest

from services.business_autonomy_envelope import (
    AutonomyMode,
    BusinessAutonomyEnvelope,
    EnvelopeState,
    EscalationTrigger,
    ResourceLimit,
    RiskBoundary,
    SuccessMetric,
    evaluate_envelope_for_action,
)


def active_envelope(**overrides):
    now = datetime.now(timezone.utc)
    data = dict(
        workspace_id="ws-1",
        objective="Increase qualified sales conversations without violating customer or policy boundaries.",
        success_metrics=(SuccessMetric("qualified_conversations", ">=", 20, minimum_confidence=0.8),),
        allowed_actions=("crm_schedule_followup", "notify_operator"),
        scope={"brand_id": "brand-1", "channels": ["whatsapp", "facebook"]},
        risk_boundary=RiskBoundary(max_risk="medium"),
        resource_limits=(ResourceLimit("followups_per_day", 50, "day"),),
        escalation_triggers=(EscalationTrigger.HIGH_RISK, EscalationTrigger.UNCERTAIN_EXTERNAL),
        mode=AutonomyMode.EXECUTE,
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(hours=1),
        state=EnvelopeState.ACTIVE,
    )
    data.update(overrides)
    return BusinessAutonomyEnvelope(**data)


def test_execute_mode_is_not_authority():
    env = active_envelope()
    assert env.mode == AutonomyMode.EXECUTE
    assert env.authority == "none"
    assert env.execution_effect == "none"
    assert env.approval_effect == "none"
    assert env.policy_effect == "none"


def test_execute_requires_explicit_allowlist():
    with pytest.raises(ValueError):
        active_envelope(allowed_actions=())


def test_active_envelope_allows_only_declared_safe_action_declaration():
    env = active_envelope()
    result = evaluate_envelope_for_action(
        env, workspace_id="ws-1", action_type="crm_schedule_followup", risk="medium"
    )
    assert result["eligible"] is True
    assert result["requires_authoritative_recheck"] is True
    assert result["authority"] == "none"


def test_undeclared_action_fails_closed():
    env = active_envelope()
    result = evaluate_envelope_for_action(
        env, workspace_id="ws-1", action_type="crm_add_contact", risk="low"
    )
    assert result["eligible"] is False
    assert "action_not_declared" in result["reasons"]


def test_workspace_mismatch_fails_closed():
    env = active_envelope()
    result = evaluate_envelope_for_action(
        env, workspace_id="ws-2", action_type="notify_operator", risk="low"
    )
    assert result["eligible"] is False
    assert "workspace_mismatch" in result["reasons"]


def test_risk_above_boundary_fails_closed():
    env = active_envelope()
    result = evaluate_envelope_for_action(
        env, workspace_id="ws-1", action_type="notify_operator", risk="high"
    )
    assert result["eligible"] is False
    assert "risk_exceeds_envelope" in result["reasons"]


def test_high_risk_can_never_become_authorized_by_envelope():
    env = active_envelope(
        risk_boundary=RiskBoundary(max_risk="high", require_approval_for=("high", "critical"))
    )
    result = evaluate_envelope_for_action(
        env, workspace_id="ws-1", action_type="notify_operator", risk="high"
    )
    assert result["eligible"] is False
    assert "approval_required_by_envelope" in result["reasons"]
    assert result["authority"] == "none"


def test_expired_or_inactive_envelope_fails_closed():
    env = active_envelope(valid_until=datetime.now(timezone.utc) - timedelta(seconds=1))
    result = evaluate_envelope_for_action(
        env, workspace_id="ws-1", action_type="notify_operator", risk="low"
    )
    assert result["eligible"] is False
    assert "envelope_not_active" in result["reasons"]


def test_fingerprint_is_stable_and_sensitive_to_contract_changes():
    a = active_envelope()
    b = active_envelope(envelope_id=a.envelope_id, valid_from=a.valid_from, valid_until=a.valid_until)
    assert a.fingerprint() == b.fingerprint()
    c = active_envelope(envelope_id=a.envelope_id, allowed_actions=("notify_operator",))
    assert a.fingerprint() != c.fingerprint()


def test_scope_is_canonicalized_for_fingerprint():
    a = active_envelope(scope={"b": 2, "a": 1})
    b = active_envelope(envelope_id=a.envelope_id, valid_from=a.valid_from, valid_until=a.valid_until, scope={"a": 1, "b": 2})
    assert a.fingerprint() == b.fingerprint()
