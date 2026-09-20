from services.business_decision_policy import evaluate_decision_candidate
from services.business_decision_action_handoff import build_action_kernel_handoff, validate_action_kernel_handoff, handoff_fingerprint


def candidate(**overrides):
    value = {
        "schema_version": "1.0", "candidate_id": "dc_123456", "understanding_id": "bu_123456",
        "workspace_id": "ws_1", "customer_id": "c_1", "candidate_action": "crm_schedule_followup",
        "risk_level": "medium", "requires_approval": False, "status": "proposed", "reason": "x",
        "intent_confidence": .9, "governance_score": .9, "governance_status": "decision_eligible",
        "resolution_status": "consensus", "disagreement": False, "constraints": [], "authority": "none",
    }
    value.update(overrides)
    return value


def test_allow_produces_ready_handoff():
    evaluation = evaluate_decision_candidate(candidate())
    handoff = build_action_kernel_handoff(evaluation)
    assert handoff["state"] == "ready"
    assert handoff["policy_disposition"] == "ALLOW"
    assert handoff["authority"] == "none"
    assert handoff["action_type"] == "crm_schedule_followup"


def test_approval_required_is_not_execution_authority():
    evaluation = evaluate_decision_candidate(candidate(risk_level="high"))
    handoff = build_action_kernel_handoff(evaluation)
    assert handoff["state"] == "approval_required"
    assert handoff["approval_required"] is True
    assert handoff["authority"] == "none"


def test_review_and_block_never_create_executable_handoff():
    for overrides, expected in [
        ({"status": "review_required", "governance_status": "needs_review"}, "blocked"),
        ({"status": "blocked", "governance_status": "insufficient_evidence"}, "blocked"),
        ({"candidate_action": "delete_customer", "risk_level": "critical"}, "blocked"),
    ]:
        evaluation = evaluate_decision_candidate(candidate(**overrides))
        handoff = build_action_kernel_handoff(evaluation)
        assert handoff["state"] == expected


def test_tampering_authority_fails_closed():
    evaluation = evaluate_decision_candidate(candidate())
    handoff = build_action_kernel_handoff(evaluation)
    handoff["authority"] = "execute"
    try:
        validate_action_kernel_handoff(handoff)
    except ValueError as exc:
        assert "authority" in str(exc)
    else:
        raise AssertionError("tampered handoff must fail")


def test_handoff_fingerprint_is_stable():
    evaluation = evaluate_decision_candidate(candidate())
    handoff = build_action_kernel_handoff(evaluation)
    assert handoff_fingerprint(handoff) == handoff_fingerprint(handoff)
