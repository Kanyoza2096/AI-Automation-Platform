from services.business_understanding import build_business_understanding, validate_business_understanding


def test_canonical_contract_prefers_bounded_ai_only_when_confident():
    value = build_business_understanding(
        text="I want to buy this, but how much is it?",
        deterministic={"intent": "purchase_interest", "confidence": 0.8, "signals": ["purchase_interest", "price_request"]},
        ai={"ai_used": True, "intent": "price_request", "confidence": 0.91,
            "evidence": ["how much is it?", "made up"], "rationale": "Customer asks price.",
            "provider": "openai_compatible", "model": "test-model"},
        message_id="m1", workspace_id="w1", channel="whatsapp", correlation_id="m1",
    )
    validate_business_understanding(value)
    assert value["schema_version"] == "1.0"
    assert value["intent"] == "price_request"
    assert value["interpretation_source"] == "ai"
    assert value["evidence"] == [{"text": "how much is it?", "source": "customer_message", "verified": True}]
    assert "made up" not in str(value["evidence"])


def test_lower_confidence_ai_cannot_override_deterministic():
    value = build_business_understanding(
        text="I want to buy this",
        deterministic={"intent": "purchase_interest", "confidence": 0.9, "signals": ["purchase_interest"]},
        ai={"ai_used": True, "intent": "general_inquiry", "confidence": 0.5, "evidence": ["I want to buy this"]},
    )
    assert value["intent"] == "purchase_interest"
    assert value["interpretation_source"] == "deterministic"


def test_low_confidence_is_explicit_uncertainty():
    value = build_business_understanding(
        text="maybe later",
        deterministic={"intent": "general_inquiry", "confidence": 0.35, "signals": ["general_inquiry"]},
    )
    validate_business_understanding(value)
    assert "low_interpretation_confidence" in value["uncertainties"]


def test_deterministic_evidence_is_preserved_for_governance():
    value = build_business_understanding(
        text="I want to buy this product",
        deterministic={"intent": "purchase_interest", "confidence": 0.9, "signals": ["purchase_interest"], "evidence": ["I want to buy this product"]},
        message_id="m-evidence", workspace_id="w-evidence", channel="whatsapp", correlation_id="m-evidence",
    )
    assert value["evidence"][0]["verified"] is True
    assert value["governance"]["decision_eligible"] is True
