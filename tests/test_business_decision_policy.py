from services.business_decision_policy import evaluate_decision_candidate, validate_decision_policy_evaluation


def base(**overrides):
    value = {
        "schema_version": "1.0", "candidate_id": "dc_123456", "understanding_id": "bu_123456",
        "workspace_id": "ws_1", "customer_id": "c_1", "candidate_action": "crm_schedule_followup",
        "risk_level": "medium", "requires_approval": False, "status": "proposed", "reason": "x",
        "intent_confidence": .9, "governance_score": .9, "governance_status": "decision_eligible",
        "resolution_status": "consensus", "disagreement": False, "constraints": [], "authority": "none",
    }
    value.update(overrides)
    return value


def test_eligible_candidate_is_allowed():
    result = evaluate_decision_candidate(base())
    validate_decision_policy_evaluation(result)
    assert result["disposition"] == "ALLOW"
    assert result["authority"] == "none"


def test_review_candidate_never_allowed():
    result = evaluate_decision_candidate(base(status="review_required", governance_status="needs_review"))
    assert result["disposition"] == "REVIEW"


def test_blocked_candidate_is_blocked():
    result = evaluate_decision_candidate(base(status="blocked", governance_status="insufficient_evidence"))
    assert result["disposition"] == "BLOCK"


def test_unresolved_disagreement_requires_review():
    result = evaluate_decision_candidate(base(disagreement=True, resolution_status="unresolved"))
    assert result["disposition"] == "REVIEW"


def test_unknown_action_fails_closed():
    result = evaluate_decision_candidate(base(candidate_action="delete_customer", risk_level="critical"))
    assert result["disposition"] == "BLOCK"


def test_tampered_authority_rejected():
    result = evaluate_decision_candidate(base())
    result["authority"] = "execute"
    try:
        validate_decision_policy_evaluation(result)
    except ValueError as exc:
        assert "authority" in str(exc)
    else:
        raise AssertionError("tampered authority must fail")
