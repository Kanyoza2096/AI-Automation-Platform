import pytest
from services.action_kernel import build_default_action_kernel
from services.business_automation import BusinessAutomationEngine
from services.business_automation_catalog import get_template


def test_whatsapp_action_is_registered_and_high_risk():
    kernel = build_default_action_kernel(db=None, allow_in_memory=True)
    assert "whatsapp_message" in kernel._contracts
    assert kernel._contracts["whatsapp_message"].risk_level == "high"
    assert kernel._contracts["whatsapp_message"].requires_approval is True


def test_followup_whatsapp_template_is_approval_controlled():
    template = get_template("sales.follow-up-whatsapp")
    assert template is not None
    assert template["action"] == "whatsapp_message"
    assert template["risk"] == "high"
    assert "{{event.phone}}" in template["parameters"]["to_number"]


def test_customer_message_event_is_a_distinct_trigger():
    assert BusinessAutomationEngine.evaluate({}, {"event_name": "customer.message.received"}) is True


def test_customer_message_interpreter_is_bounded_and_deterministic():
    from services.customer_message_interpreter import interpret_customer_message
    result = interpret_customer_message("Yes, I'm interested. How much does it cost?")
    assert result["intent"] in {"price_request", "purchase_interest"}
    assert result["interpreted"] is True
    assert 0 < result["confidence"] <= 1


def test_customer_opt_out_has_priority():
    from services.customer_message_interpreter import interpret_customer_message
    result = interpret_customer_message("I'm interested, but stop contacting me")
    assert result["intent"] == "opt_out"
