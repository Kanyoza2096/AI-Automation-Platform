from services.business_learning_decision_context import (
    build_learning_decision_context,
    validate_learning_decision_context,
)


def decision_input():
    return {
        "schema_version": "1.0", "understanding_id": "u1", "workspace_id": "w1",
        "message_id": "m1", "customer_id": "c1", "channel": "whatsapp",
        "correlation_id": "corr1", "intent": "purchase_interest", "intent_confidence": .9,
        "signals": ["PURCHASE_INTENT"], "governance_status": "decision_eligible",
        "decision_eligible": True, "interpretation_risk": "low", "governance_score": .9,
        "resolution_status": "consensus", "disagreement": False, "authority": "none",
    }


def learning(**overrides):
    value = {
        "schema_version": "1.0", "knowledge_id": "k1", "workspace_id": "w1",
        "target_type": "action", "target_id": "crm_schedule_followup",
        "promotion_state": "promoted", "direction": "positive", "confidence": .9,
        "support_count": 5, "consistency": .8, "evidence": {},
        "lesson": "advisory lesson", "authority": "none",
    }
    value.update(overrides)
    return value


def test_only_promoted_learning_enters_context():
    result = build_learning_decision_context(decision_input(), [learning()])
    assert result["knowledge_count"] == 1
    assert result["advisory_only"] is True
    assert result["authority"] == "none"
    validate_learning_decision_context(result)


def test_candidate_learning_is_rejected():
    try:
        build_learning_decision_context(decision_input(), [learning(promotion_state="candidate")])
    except ValueError as exc:
        assert "promoted" in str(exc)
    else:
        raise AssertionError("candidate learning must not enter decision context")


def test_cross_workspace_learning_is_rejected():
    try:
        build_learning_decision_context(decision_input(), [learning(workspace_id="w2")])
    except ValueError as exc:
        assert "workspace" in str(exc)
    else:
        raise AssertionError("cross-workspace learning must be rejected")


def test_learning_cannot_grant_authority():
    try:
        build_learning_decision_context(decision_input(), [learning(authority="execute")])
    except ValueError as exc:
        assert "authority" in str(exc)
    else:
        raise AssertionError("non-none authority must fail closed")


def test_context_is_bounded_to_ten_items():
    items = [learning(knowledge_id=f"k{i}") for i in range(20)]
    result = build_learning_decision_context(decision_input(), items)
    assert result["knowledge_count"] == 10


def test_context_cannot_change_policy_approval_or_execution():
    result = build_learning_decision_context(decision_input(), [])
    assert result["policy_effect"] == "none"
    assert result["approval_effect"] == "none"
    assert result["execution_effect"] == "none"
