from services.action_kernel import build_default_action_kernel
from services.reconciliation_orchestrator import ReconciliationOrchestrator
from services.sector_action_implementation_gate import build_sector_action_implementation_gate
from services.sector_action_contract_registry import build_sector_action_contract_registry
from services.sector_provider_binding_contracts import build_sector_provider_binding_contracts
from plugins.industries.manufacturing.repository import ManufacturingRepository
from services.sector_manufacturing_maintenance_execution import register_manufacturing_maintenance_action
from services.sector_manufacturing_maintenance_reconciliation import ManufacturingMaintenanceReconciliationService


def test_manufacturing_maintenance_is_canonical_and_bound():
    registry = build_sector_action_contract_registry()
    rec = next(x for x in registry["contracts"] if x["sector_action"] == "manufacturing.request_maintenance")
    assert rec["action_kernel"]["binding"] == "BOUND"
    assert rec["governance"]["risk_level"] == "high"
    assert rec["governance"]["requires_approval"] is True
    assert rec["connector"]["provider"] == "jira"
    assert rec["verification"]["code_backed"] is True
    assert rec["reconciliation"]["code_backed"] is True


def test_manufacturing_binding_contract_is_explicit():
    data = build_sector_provider_binding_contracts()
    b = next(x for x in data["bindings"] if x["sector_action"] == "manufacturing.request_maintenance")
    assert b["status"] == "BOUND"
    assert "JIRA_MAINTENANCE_BINDING" in b["satisfied_gates"]


def test_manufacturing_gate_is_ready():
    gate = build_sector_action_implementation_gate()
    row = next(x for x in gate["actions"] if x["sector_action"] == "manufacturing.request_maintenance")
    assert row["gate"]["status"] == "READY_FOR_CANONICAL_EXECUTION_PATH"
    assert row["provider_candidates"][0]["provider_action"] == "jira.create_issue"


def test_reconciliation_never_creates_second_issue():
    class Jira:
        def __init__(self): self.calls = 0
        def get_issue(self, key): self.calls += 1; return {"id":"123","key":key,"fields":{}}
    repo = ManufacturingRepository()
    ws = "ws-1"
    wc = repo.create_work_center(ws, {"name":"Line A"})
    mo = repo.create_maintenance(ws, {"work_center_id":wc.id,"description":"Bearing replacement"})
    repo.bind_maintenance_provider(ws, "jira", {"project_key":"OPS","issue_type":"Task"})
    req = repo.create_maintenance_request(ws, mo.id, {"project_key":"OPS","issue_type":"Task","reason":"bearing failure"})
    repo.record_maintenance_request_receipt(ws, req.id, "req-1", {"status":"uncertain","metadata":{"issue_key":"OPS-7"}})
    jira=Jira()
    result=ManufacturingMaintenanceReconciliationService(repo,jira).reconcile(workspace_id=ws,request_id=req.id)
    assert result["terminal"] is True
    assert jira.calls == 1
    assert repo.get_maintenance_request(ws,req.id).status == "succeeded"

import asyncio

class FakeJira:
    def __init__(self): self.created=[]; self.issue={"id":"100","key":"OPS-7","fields":{}}
    def create_issue(self, **kwargs):
        self.created.append(kwargs)
        return {"status":"uncertain","object_id":"100","metadata":{"issue_key":"OPS-7"},"reconciliation_required":True}
    def get_issue(self, issue_key): return dict(self.issue, key=issue_key)


def test_action_kernel_maintenance_request_is_uncertain_until_jira_verification():
    repo=ManufacturingRepository(); ws="ws-2"
    wc=repo.create_work_center(ws,{"name":"Line B"})
    mo=repo.create_maintenance(ws,{"work_center_id":wc.id,"description":"Pump service","priority":"high"})
    repo.bind_maintenance_provider(ws,"jira",{"project_key":"OPS","issue_type":"Task"})
    jira=FakeJira()
    kernel=build_default_action_kernel(allow_in_memory=True, manufacturing_repository=repo, jira_adapter=jira)
    result=asyncio.run(kernel.execute(workspace_id=ws, action_type="manufacturing_request_maintenance", parameters={"maintenance_id":mo.id,"reason":"scheduled service"}, risk_level="high", decision_id="d-m1", source="test"))
    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True
    assert len(jira.created)==1
    request_id=result["output"]["maintenance_request_id"]
    out=ReconciliationOrchestrator().reconcile_manufacturing_maintenance(repository=repo,jira_adapter=jira,workspace_id=ws,request_id=request_id)
    assert out["terminal"] is True and out["status"] == "succeeded"
    assert len(jira.created)==1
