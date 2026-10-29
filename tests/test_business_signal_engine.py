from services.business_signal_engine import normalize_business_signals, validate_signal_set


def bu(intent, confidence=.9, signals=None, evidence=None):
    return {
        "understanding_id": "bu_test", "message_id": "m1", "workspace_id": "w1",
        "intent": intent, "intent_confidence": confidence,
        "signals": signals or [intent],
        "evidence": evidence or [{"text": "I want to buy this", "verified": True}],
    }


def test_price_request_becomes_kdo_owned_signals():
    value = normalize_business_signals(bu("price_request", signals=["price_request"]))
    validate_signal_set(value)
    assert "PRICING_REQUEST" in value["signal_names"]
    assert "PRICE_SENSITIVITY" in value["signal_names"]
    assert "price_request" not in value["signal_names"]


def test_purchase_and_price_preserve_multiple_business_meanings():
    value = normalize_business_signals(bu("purchase_interest", signals=["purchase_interest", "price_request"]))
    assert "PURCHASE_INTENT" in value["signal_names"]
    assert "PRICING_REQUEST" in value["signal_names"]
    assert "PRICE_SENSITIVITY" in value["signal_names"]


def test_opt_out_is_explicit_compliance_priority():
    value = normalize_business_signals(bu("opt_out", signals=["opt_out", "purchase_interest"]))
    assert value["compliance_priority"] == "OPT_OUT"
    assert "OPT_OUT" in value["signal_names"]


def test_unverified_evidence_never_enters_signal():
    value = normalize_business_signals(bu("price_request", evidence=[{"text": "invented", "verified": False}]))
    assert all(not item["evidence"] for item in value["signals"])
