from services.business_understanding import build_business_understanding, validate_business_understanding
from services.business_understanding_governance import govern_business_understanding, validate_governance


def test_high_confidence_without_evidence_is_not_decision_eligible():
    value = govern_business_understanding({
        "intent": "price_request", "intent_confidence": 0.99,
        "evidence": [], "message_id": "m1", "workspace_id": "w1",
        "channel": "whatsapp", "correlation_id": "m1",
    })
    validate_governance(value)
    assert value["status"] == "insufficient_evidence"
    assert value["decision_eligible"] is False


def test_verified_evidence_and_context_can_be_decision_eligible():
    value = govern_business_understanding({
        "intent": "purchase_interest", "intent_confidence": 0.92,
        "evidence": [{"text": "I want to buy this", "verified": True}],
        "message_id": "m1", "workspace_id": "w1",
        "channel": "whatsapp", "correlation_id": "m1",
    })
    assert value["status"] == "decision_eligible"
    assert value["decision_eligible"] is True
    assert value["authority"] == "none"


def test_low_confidence_needs_review_even_with_evidence():
    value = govern_business_understanding({
        "intent": "meeting_request", "intent_confidence": 0.60,
        "evidence": [{"text": "Can we meet tomorrow?", "verified": True}],
        "message_id": "m1", "workspace_id": "w1",
        "channel": "whatsapp", "correlation_id": "m1",
    })
    assert value["status"] == "needs_review"


def test_opt_out_is_governed_without_granting_authority():
    value = govern_business_understanding({
        "intent": "opt_out", "intent_confidence": 0.76,
        "evidence": [{"text": "stop messaging me", "verified": True}],
        "message_id": "m1", "workspace_id": "w1",
        "channel": "whatsapp", "correlation_id": "m1",
    })
    assert value["interpretation_risk"] == "high"
    assert value["authority"] == "none"


def test_canonical_understanding_contains_governance():
    value = build_business_understanding(
        text="I want to buy this",
        deterministic={"intent": "purchase_interest", "confidence": 0.9, "signals": ["purchase_interest"]},
        message_id="m1", workspace_id="w1", channel="whatsapp", correlation_id="m1",
    )
    validate_business_understanding(value)
    assert value["governance"]["schema_version"] == "1.0"
    assert value["governance"]["authority"] == "none"
