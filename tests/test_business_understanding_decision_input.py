import pytest
from services.business_understanding import build_business_understanding
from services.business_understanding_decision_input import build_decision_input, validate_decision_input


def _build(**kwargs):
    return build_business_understanding(
        text=kwargs.pop("text", "I want to buy this product at $100"),
        deterministic=kwargs.pop("deterministic", {"intent": "purchase_interest", "confidence": 0.9, "signals": ["purchase_interest"], "evidence": ["I want to buy this product"]}),
        message_id="m1", workspace_id="w1", customer_id="c1", channel="whatsapp", correlation_id="m1",
        **kwargs,
    )


def test_projection_is_decision_plane_safe():
    value = _build()
    out = build_decision_input(value)
    validate_decision_input(out)
    assert out["authority"] == "none"
    assert out["decision_eligible"] is True
    assert "model" not in out
    assert "provider" not in out
    assert "evidence" not in out


def test_missing_evidence_is_not_decision_eligible():
    value = _build(text="hello", deterministic={"intent": "general_inquiry", "confidence": 0.99, "signals": []})
    out = build_decision_input(value)
    assert out["decision_eligible"] is False
    assert out["governance_status"] == "insufficient_evidence"


def test_projection_cannot_be_modified_into_authority():
    value = _build()
    out = build_decision_input(value)
    out["authority"] = "execute"
    with pytest.raises(ValueError):
        validate_decision_input(out)
