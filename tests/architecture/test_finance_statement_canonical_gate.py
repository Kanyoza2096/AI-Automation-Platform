from services.action_kernel import build_default_action_kernel
from services.sector_action_contract_registry import build_sector_action_contract_registry
from services.sector_action_implementation_gate import build_sector_action_implementation_gate
from services.sector_provider_binding_contracts import build_sector_provider_binding_contracts
from plugins.industries.finance.repository import FinanceRepository
from services.sector_finance_statement_reconciliation import FinanceStatementReconciliationService

class FakeTwilio:
    def __init__(self, status="accepted"):
        self.status=status; self.sent=[]
    def send_sms(self, *, from_, to, body):
        self.sent.append((from_,to,body)); return {"status": self.status, "provider_object_id": "SM123"}
    def get_message(self, ref):
        return {"sid": ref, "status": "delivered"}

def test_statement_is_ready_and_explicitly_bound():
    reg=build_sector_action_contract_registry(); row=next(x for x in reg["contracts"] if x["sector_action"]=="finance.send_statement")
    assert row["governed_action_type"]=="finance_send_statement"
    assert row["action_kernel"]["binding"]=="BOUND"
    assert row["connector"]["binding"]=="BOUND" and row["connector"]["provider_action"]=="twilio.send_sms"
    assert row["receipt"]!="NOT_EXPOSED" and row["verification"]!="NOT_EXPOSED" and row["reconciliation"]!="NOT_EXPOSED"

def test_statement_implementation_gate_is_ready():
    gate=build_sector_action_implementation_gate(); row=next(x for x in gate["actions"] if x["sector_action"]=="finance.send_statement")
    assert row["gate"]["status"]=="READY_FOR_CANONICAL_EXECUTION_PATH"
    assert gate["summary"]["ready"]==35

def test_statement_binding_contract_is_durable_evidence():
    row=next(x for x in build_sector_provider_binding_contracts()["bindings"] if x["sector_action"]=="finance.send_statement")
    assert row["status"]=="BOUND"
    assert "DURABLE_STATEMENT_RECONCILIATION_EVIDENCE" in row["satisfied_gates"]

def test_statement_execution_is_uncertain_until_authoritative_delivery():
    repo=FinanceRepository(); account=repo.create_account("fin-a", {"name":"Operating"})
    twilio=FakeTwilio(); kernel=build_default_action_kernel(allow_in_memory=True, finance_statement_repository=repo, twilio_adapter=twilio)
    class Intent:
        workspace_id="fin-a"; id="intent-1"
    import asyncio
    result=asyncio.run(kernel.execute(workspace_id="fin-a", action_type="finance_send_statement", parameters={"account_id":account.id,"to_number":"+265991234567","message":"Statement ready","from_":"+265700000000","reference":"stmt-1"}, risk_level="high", decision_id="dec-1", source="test"))
    assert result["status"]=="uncertain"
    assert result["output"]["provider_receipt_is_business_outcome"] is False
    assert result["output"]["reconciliation_required"] is True
    rec=FinanceStatementReconciliationService(repo, twilio).reconcile(workspace_id="fin-a", statement_delivery_id=result["output"]["statement_delivery_id"])
    assert rec["status"]=="succeeded" and rec["terminal"] is True
    assert rec["statement_delivery_is_business_outcome"] is False
