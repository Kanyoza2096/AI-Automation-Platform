import asyncio
from services.action_kernel import build_default_action_kernel
from services.reconciliation_orchestrator import ReconciliationOrchestrator
from plugins.industries.energy.repository import EnergyRepository

class FakeJira:
    def __init__(self):
        self.created = []
        self.issue = {"id": "9101", "key": "ENG-42", "fields": {"summary": "Crew dispatch"}}
    def create_issue(self, *, project_key, issue_type, summary, description=""):
        self.created.append((project_key, issue_type, summary, description))
        return {"status": "succeeded", "object_id": "9101", "request_id": "req-eng-42", "metadata": {"issue_key": "ENG-42"}}
    def get_issue(self, issue_key):
        assert issue_key == "ENG-42"
        return self.issue

def _kernel(repo, jira):
    return build_default_action_kernel(allow_in_memory=True, energy_repository=repo, jira_adapter=jira)

def test_energy_crew_dispatch_is_uncertain_until_reconciled():
    repo, jira = EnergyRepository(), FakeJira()
    asset = repo.create_asset("eng-ws", {"name":"Substation A", "asset_type":"substation"})
    wo = repo.create_work_order("eng-ws", {"asset_id":asset.id, "description":"Inspect feeder fault", "priority":"high"})
    repo.bind_crew_dispatch_provider("eng-ws", "jira", {"project_key":"ENG", "issue_type":"Task"})
    result = asyncio.run(_kernel(repo,jira).execute(workspace_id="eng-ws", action_type="energy_dispatch_crew", parameters={"work_order_id":wo.id,"crew_id":"crew-7"}, risk_level="high", decision_id="decision-eng-1", source="test"))
    assert result["status"] == "uncertain"
    assert result["output"]["reconciliation_required"] is True
    assert result["output"]["provider_receipt_is_business_outcome"] is False
    dispatch_id = result["output"]["dispatch_id"]
    reconciled = ReconciliationOrchestrator().reconcile_energy_crew_dispatch(repository=repo, jira_adapter=jira, workspace_id="eng-ws", dispatch_id=dispatch_id)
    assert reconciled["status"] == "succeeded"
    assert reconciled["terminal"] is True
    assert len(jira.created) == 1

def test_missing_energy_crew_binding_fails_closed_before_provider_mutation():
    repo, jira = EnergyRepository(), FakeJira()
    asset = repo.create_asset("eng-ws", {"name":"Substation A", "asset_type":"substation"})
    wo = repo.create_work_order("eng-ws", {"asset_id":asset.id, "description":"Inspect feeder fault"})
    result = asyncio.run(_kernel(repo,jira).execute(workspace_id="eng-ws", action_type="energy_dispatch_crew", parameters={"work_order_id":wo.id,"crew_id":"crew-7"}, risk_level="high", decision_id="decision-eng-2", source="test"))
    assert result["status"] == "uncertain"
    assert jira.created == []

def test_energy_crew_reconciliation_never_dispatches_again():
    repo, jira = EnergyRepository(), FakeJira()
    asset = repo.create_asset("eng-ws", {"name":"Substation A", "asset_type":"substation"})
    wo = repo.create_work_order("eng-ws", {"asset_id":asset.id, "description":"Inspect feeder fault"})
    repo.bind_crew_dispatch_provider("eng-ws", "jira", {"project_key":"ENG", "issue_type":"Task"})
    result = asyncio.run(_kernel(repo,jira).execute(workspace_id="eng-ws", action_type="energy_dispatch_crew", parameters={"work_order_id":wo.id,"crew_id":"crew-7"}, risk_level="high", decision_id="decision-eng-3", source="test"))
    dispatch_id = result["output"]["dispatch_id"]
    orch = ReconciliationOrchestrator()
    orch.reconcile_energy_crew_dispatch(repository=repo, jira_adapter=jira, workspace_id="eng-ws", dispatch_id=dispatch_id)
    orch.reconcile_energy_crew_dispatch(repository=repo, jira_adapter=jira, workspace_id="eng-ws", dispatch_id=dispatch_id)
    assert len(jira.created) == 1
