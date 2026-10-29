from services.sector_action_contract_registry import build_sector_action_contract_registry
from services.sector_action_implementation_gate import build_sector_action_implementation_gate
from services.sector_provider_binding_contracts import build_sector_provider_binding_contracts
from services.sector_finance_payment_execution import FINANCE_PAYMENT_ACTION_CONTRACT_SPEC


def test_finance_payment_contract_is_bound_end_to_end():
    registry = build_sector_action_contract_registry()
    record = next(x for x in registry["contracts"] if x["sector_action"] == "finance.initiate_payment")
    assert record["action_kernel"]["binding"] == "BOUND"
    assert record["governance"]["risk_level"] == "critical"
    assert record["governance"]["requires_approval"] is True
    assert record["connector"]["provider"] == "stripe"
    assert record["receipt"]["outcome"] == "uncertain_until_payment_lifecycle_reconciled"
    assert record["verification"]["terminal_provider_states"] == ["succeeded", "canceled"]
    assert record["reconciliation"]["strategy"] == "FinancePaymentReconciliationService"


def test_finance_provider_binding_is_bound_with_all_gates_satisfied():
    binding = next(x for x in build_sector_provider_binding_contracts()["bindings"] if x["sector_action"] == "finance.initiate_payment")
    assert binding["status"] == "BOUND"
    assert set(binding["required_gates"]) <= set(binding["satisfied_gates"])


def test_finance_payment_is_ready_in_implementation_gate():
    gate = build_sector_action_implementation_gate()
    record = next(x for x in gate["actions"] if x["sector_action"] == "finance.initiate_payment")
    assert record["gate"]["status"] == "READY_FOR_CANONICAL_EXECUTION_PATH"
    assert record["gate"]["blockers"] == []
    assert record["provider_candidates"][0]["status"] == "CANDIDATE_BOUND"
