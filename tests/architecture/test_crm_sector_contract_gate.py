from services.sector_action_contract_registry import build_sector_action_contract_registry
from services.sector_action_implementation_gate import build_sector_action_implementation_gate
from services.sector_provider_binding_contracts import build_sector_provider_binding_contracts


def _crm(registry):
    return next(x for x in registry["contracts"] if x["sector_action"] == "crm.send_message")


def test_crm_message_contract_is_code_backed():
    record = _crm(build_sector_action_contract_registry())
    assert record["action_kernel"]["binding"] == "BOUND"
    assert record["governance"]["risk_level"] == "high"
    assert record["governance"]["requires_approval"] is True
    assert record["connector"]["provider_action"] == "twilio.send_sms"
    assert record["receipt"]["outcome"] == "uncertain_until_delivery_reconciled"
    assert record["verification"]["strategy"] == "TwilioAdapter.get_message"
    assert record["reconciliation"]["strategy"] == "CRMMessageReconciliationService"


def test_crm_message_provider_binding_is_bound():
    item = next(x for x in build_sector_provider_binding_contracts()["bindings"] if x["sector_action"] == "crm.send_message")
    assert item["status"] == "BOUND"
    assert item["governed_provider_action"] == "twilio.send_sms"
    assert set(item["required_gates"]).issubset(set(item["satisfied_gates"]))


def test_crm_message_implementation_gate_is_ready():
    item = next(x for x in build_sector_action_implementation_gate()["actions"] if x["sector_action"] == "crm.send_message")
    assert item["gate"]["status"] == "READY_FOR_CANONICAL_EXECUTION_PATH"
    assert item["gate"]["blockers"] == []
    assert item["provider_candidates"][0]["status"] == "CANDIDATE_BOUND"
