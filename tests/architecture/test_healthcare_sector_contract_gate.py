from services.sector_action_contract_registry import build_sector_action_contract_registry
from services.sector_action_implementation_gate import build_sector_action_implementation_gate
from services.sector_provider_binding_contracts import build_sector_provider_binding_contracts

def test_healthcare_patient_message_is_canonical_and_bound():
    registry=build_sector_action_contract_registry(); rec=next(x for x in registry["contracts"] if x["sector_action"]=="healthcare.send_patient_message")
    assert rec["action_kernel"]["binding"]=="BOUND"
    assert rec["governance"]["risk_level"]=="high"
    assert rec["governance"]["requires_approval"] is True
    assert rec["connector"]["provider_action"]=="twilio.send_sms"
    assert rec["receipt"]["protocol"]=="ProviderReceipt"
    assert rec["verification"]["strategy"]=="TwilioAdapter.get_message"
    assert rec["reconciliation"]["strategy"]=="HealthcarePatientMessageReconciliationService"

def test_healthcare_provider_binding_is_bound():
    x=build_sector_provider_binding_contracts(); rec=next(x for x in x["bindings"] if x["sector_action"]=="healthcare.send_patient_message")
    assert rec["status"]=="BOUND"; assert set(rec["satisfied_gates"]) == set(rec["required_gates"]) | {"DURABLE_PATIENT_MESSAGE_RECONCILIATION_EVIDENCE"}

def test_healthcare_action_gate_is_ready():
    x=build_sector_action_implementation_gate(); rec=next(x for x in x["actions"] if x["sector_action"]=="healthcare.send_patient_message")
    assert rec["gate"]["status"]=="READY_FOR_CANONICAL_EXECUTION_PATH"
    assert rec["provider_candidates"][0]["status"]=="CANDIDATE_BOUND"
