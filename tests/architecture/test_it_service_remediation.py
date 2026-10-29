import asyncio
from datetime import datetime, timezone

from plugins.industries.it_operations.repository import ITOperationsRepository
from services.action_kernel import build_default_action_kernel
from plugins.industries.it_operations.repository import ITOperationsRepository
from services.reconciliation_orchestrator import ReconciliationOrchestrator

class FakeGitHub:
    def __init__(self): self.dispatches=[]; self.runs=[]
    def dispatch_workflow(self, owner, repo, *, workflow_id, ref, inputs=None, confirm=False):
        self.dispatches.append((owner, repo, workflow_id, ref, inputs, confirm))
        return {"status":"uncertain","request_id":"gh-req-1","metadata":{"reconciliation_required":True}}
    def reconcile_workflow(self, owner, repo, *, workflow_id, ref, started_after, per_page=100):
        return {"provider":"github","status":"matched","matches":list(self.runs),"authoritative_absence":False}

def _setup():
    repo=ITOperationsRepository(); svc=repo.add_service("ws-1", {"name":"api","environment":"production"})
    repo.bind_service_provider("ws-1", svc.id, {"provider":"github","provider_service_id":"github-repo","environment":"production","metadata":{"owner":"acme","repo":"platform","workflow_id":"remediate.yml","ref":"main"}})
    return repo, svc

def test_remediation_dispatch_is_uncertain_until_workflow_reconciles():
    repo, svc = _setup(); gh=FakeGitHub()
    kernel=build_default_action_kernel(allow_in_memory=True, it_repository=repo, github_adapter=gh)
    result=asyncio.run(kernel.execute(workspace_id="ws-1", action_type="it_remediate_service", parameters={"service_id":svc.id,"reason":"restart unhealthy worker"}, risk_level="critical", decision_id="d-1", source="test"))
    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True
    assert gh.dispatches[0][0:4] == ("acme","platform","remediate.yml","main")
    remediation_id=result["output"]["remediation_id"]
    assert repo.get_service_remediation("ws-1", remediation_id).status == "uncertain"

def test_reconciliation_requires_unique_authoritative_run_and_never_dispatches_again():
    repo, svc = _setup(); gh=FakeGitHub(); kernel=build_default_action_kernel(allow_in_memory=True, it_repository=repo, github_adapter=gh)
    result=asyncio.run(kernel.execute(workspace_id="ws-1", action_type="it_remediate_service", parameters={"service_id":svc.id,"reason":"restart unhealthy worker"}, risk_level="critical", decision_id="d-1", source="test"))
    rid=result["output"]["remediation_id"]
    orch=ReconciliationOrchestrator()
    gh.runs=[{"id":41,"status":"completed","conclusion":"success","created_at":datetime.now(timezone.utc).isoformat(),"head_branch":"main"}]
    out=orch.reconcile_it_service_remediation(repository=repo, github_adapter=gh, workspace_id="ws-1", remediation_id=rid)
    assert out["terminal"] is True and out["status"] == "succeeded"
    assert len(gh.dispatches) == 1

def test_reconciliation_preserves_ambiguity_when_multiple_runs_match():
    repo, svc = _setup(); gh=FakeGitHub(); kernel=build_default_action_kernel(allow_in_memory=True, it_repository=repo, github_adapter=gh)
    result=asyncio.run(kernel.execute(workspace_id="ws-1", action_type="it_remediate_service", parameters={"service_id":svc.id,"reason":"restart unhealthy worker"}, risk_level="critical", decision_id="d-1", source="test"))
    rid=result["output"]["remediation_id"]
    now=datetime.now(timezone.utc).isoformat(); gh.runs=[{"id":41,"status":"completed","conclusion":"success","created_at":now,"head_branch":"main"},{"id":42,"status":"completed","conclusion":"success","created_at":now,"head_branch":"main"}]
    out=ReconciliationOrchestrator().reconcile_it_service_remediation(repository=repo, github_adapter=gh, workspace_id="ws-1", remediation_id=rid)
    assert out["terminal"] is False and out["status"] == "in_progress"
    assert repo.get_service_remediation("ws-1", rid).status == "uncertain"
