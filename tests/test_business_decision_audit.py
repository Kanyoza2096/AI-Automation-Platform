from services.business_decision_audit import DecisionAuditLedger, build_audit_record, make_idempotency_key
from services.business_decision_policy import evaluate_decision_candidate


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


def test_same_evaluation_is_idempotent():
    evaluation = evaluate_decision_candidate(candidate())
    ledger = DecisionAuditLedger()
    first, replay1 = ledger.record(evaluation)
    second, replay2 = ledger.record(evaluation)
    assert replay1 is False
    assert replay2 is True
    assert second == first


def test_idempotency_key_is_stable():
    evaluation = evaluate_decision_candidate(candidate())
    assert make_idempotency_key(evaluation) == make_idempotency_key(evaluation)


def test_conflicting_evaluation_same_key_fails_closed():
    evaluation = evaluate_decision_candidate(candidate())
    ledger = DecisionAuditLedger()
    ledger.record(evaluation)
    conflict = dict(evaluation, disposition="BLOCK")
    try:
        ledger.record(conflict)
    except RuntimeError as exc:
        assert "idempotency conflict" in str(exc)
    else:
        raise AssertionError("conflicting evaluation must fail closed")


def test_audit_cannot_grant_authority():
    evaluation = evaluate_decision_candidate(candidate())
    record = build_audit_record(evaluation)
    record["authority"] = "execute"
    try:
        from services.business_decision_audit import validate_audit_record
        validate_audit_record(record)
    except ValueError as exc:
        assert "authority" in str(exc)
    else:
        raise AssertionError("tampered audit authority must fail")
