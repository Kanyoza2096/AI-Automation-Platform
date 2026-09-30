from services.business_decision_candidate import build_decision_candidate, validate_decision_candidate


def base(**overrides):
    value = {
        "schema_version": "1.0", "understanding_id": "bu_12345678901234567890",
        "message_id": "m1", "workspace_id": "ws1", "customer_id": "c1",
        "channel": "whatsapp", "correlation_id": "m1", "intent": "purchase_interest",
        "intent_confidence": 0.91, "signals": ["purchase_interest"],
        "governance_status": "decision_eligible", "decision_eligible": True,
        "interpretation_risk": "low", "governance_score": 0.86,
        "resolution_status": "consensus", "disagreement": False, "authority": "none",
    }
    value.update(overrides)
    return value


def test_eligible_input_produces_non_authoritative_candidate():
    candidate = build_decision_candidate(base())
    validate_decision_candidate(candidate)
    assert candidate["status"] == "proposed"
    assert candidate["candidate_action"] == "crm_schedule_followup"
    assert candidate["authority"] == "none"


def test_needs_review_never_becomes_proposed():
    candidate = build_decision_candidate(base(governance_status="needs_review", decision_eligible=False))
    assert candidate["status"] == "review_required"
    assert "understanding_not_decision_eligible" in candidate["constraints"]


def test_insufficient_evidence_is_blocked():
    candidate = build_decision_candidate(base(governance_status="insufficient_evidence", decision_eligible=False))
    assert candidate["status"] == "blocked"


def test_disagreement_remains_visible():
    candidate = build_decision_candidate(base(disagreement=True, resolution_status="deterministic_preferred"))
    assert candidate["disagreement"] is True
    assert "ai_deterministic_disagreement_present" in candidate["constraints"]


def test_authority_tampering_fails():
    candidate = build_decision_candidate(base())
    candidate["authority"] = "execute"
    try:
        validate_decision_candidate(candidate)
    except ValueError as exc:
        assert "authority" in str(exc)
    else:
        raise AssertionError("tampered candidate must fail closed")
