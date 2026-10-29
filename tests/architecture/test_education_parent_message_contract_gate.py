from services.sector_action_contract_registry import build_sector_action_contract_registry
from services.sector_action_implementation_gate import build_sector_action_implementation_gate
from services.sector_provider_binding_contracts import build_sector_provider_binding_contracts

def test_education_parent_message_is_canonical_ready():
    rec=next(x for x in build_sector_action_contract_registry()["contracts"] if x["sector_action"]=="education.send_parent_message")
    assert rec["action_kernel"]["binding"]=="BOUND"; assert rec["governance"]["risk_level"]=="high"; assert rec["governance"]["requires_approval"] is True
    assert rec["connector"]["binding"]=="BOUND"; assert rec["receipt"]["protocol"]=="ProviderReceipt"; assert rec["verification"]["strategy"]=="TwilioAdapter.get_message"; assert rec["reconciliation"]["code_backed"] is True
    gate=build_sector_action_implementation_gate(); row=next(x for x in gate["actions"] if x["sector_action"]=="education.send_parent_message")
    assert row["gate"]["status"]=="READY_FOR_CANONICAL_EXECUTION_PATH"; assert row["gate"]["blockers"]==[]; assert row["provider_candidates"][0]["status"]=="CANDIDATE_BOUND"
    binding=next(x for x in build_sector_provider_binding_contracts()["bindings"] if x["sector_action"]=="education.send_parent_message")
    assert binding["status"]=="BOUND"; assert "DURABLE_PARENT_MESSAGE_RECONCILIATION_EVIDENCE" in binding["satisfied_gates"]
