from services.business_understanding import build_business_understanding, validate_business_understanding
from services.business_understanding_resolution import resolve_business_understanding, validate_resolution


def test_agreement_is_consensus():
    r = resolve_business_understanding(
        deterministic={"intent": "price_request", "confidence": .8},
        ai={"ai_used": True, "intent": "price_request", "confidence": .7, "evidence": ["price"]},
        text="What is the price?",
    )
    assert r["status"] == "consensus"
    assert r["selected_intent"] == "price_request"


def test_weak_ai_disagreement_keeps_deterministic():
    r = resolve_business_understanding(
        deterministic={"intent": "price_request", "confidence": .8},
        ai={"ai_used": True, "intent": "purchase_interest", "confidence": .82, "evidence": ["maybe"]},
        text="Maybe I am interested",
    )
    assert r["status"] == "deterministic_preferred"
    assert r["selected_intent"] == "price_request"
    assert r["disagreement"] is True


def test_material_ai_evidence_can_resolve_disagreement():
    r = resolve_business_understanding(
        deterministic={"intent": "general_inquiry", "confidence": .30},
        ai={"ai_used": True, "intent": "purchase_interest", "confidence": .90, "evidence": ["I want to buy this"]},
        text="I want to buy this",
    )
    assert r["status"] == "ai_supported_resolution"
    assert r["selected_intent"] == "purchase_interest"
    assert r["ai_verified_evidence_count"] == 1


def test_opt_out_is_safety_sensitive_with_exact_evidence():
    r = resolve_business_understanding(
        deterministic={"intent": "general_inquiry", "confidence": .9},
        ai={"ai_used": True, "intent": "opt_out", "confidence": .71, "evidence": ["stop messaging me"]},
        text="Please stop messaging me",
    )
    assert r["status"] == "safety_sensitive_ai"
    assert r["selected_intent"] == "opt_out"
    assert r["authority"] == "none"


def test_canonical_understanding_contains_resolution_and_validates():
    value = build_business_understanding(
        text="What is the price?",
        deterministic={"intent": "price_request", "confidence": .9, "signals": ["price_request"]},
        ai={"ai_used": True, "intent": "purchase_interest", "confidence": .91, "evidence": ["price"], "rationale": "Customer asks price."},
        message_id="m1", workspace_id="w1", channel="whatsapp", correlation_id="m1",
    )
    validate_business_understanding(value)
    validate_resolution(value["resolution"])
    assert value["resolution"]["disagreement"] is True
