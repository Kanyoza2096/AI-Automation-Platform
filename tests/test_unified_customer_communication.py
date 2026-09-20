from services.action_kernel import build_default_action_kernel
from services.business_automation import BusinessAutomationEngine
from services.business_automation_catalog import get_template


def test_unified_customer_communication_is_high_risk_and_approval_controlled():
    kernel = build_default_action_kernel(db=None, allow_in_memory=True)
    contract = kernel._contracts["customer_communication"]
    assert contract.risk_level == "high"
    assert contract.requires_approval is True
    assert set(contract.required_parameters) == {"channel", "recipient", "message"}


def test_unified_sales_template_exists():
    template = get_template("sales.follow-up-unified")
    assert template is not None
    assert template["action"] == "customer_communication"
    assert template["trigger"] == "lead.follow_up_due"
    assert template["risk"] == "high"


def test_business_automation_registers_unified_communication():
    engine = BusinessAutomationEngine()
    assert "customer_communication" in engine._actions
