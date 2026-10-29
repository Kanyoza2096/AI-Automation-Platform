from plugins.industries.it_operations.models import Deployment, Service, ServiceProviderBinding
from plugins.industries.it_operations.repository import ITOperationsRepository
from services.reconciliation_orchestrator import ReconciliationOrchestrator, ReconciliationPolicy
from services.sector_it_deployment_reconciliation import ITDeploymentReconciliationService
from services.it_deployment_evidence_read_model import build_it_deployment_evidence

class FakeRender:
    def __init__(self, status): self.status = status; self.calls = 0
    def reconcile_deploy(self, service_id, ref):
        self.calls += 1
        return {"status": self.status, "deploy_id": ref}

def setup_repo():
    r=ITOperationsRepository()
    r._services["svc"] = Service(id="svc", workspace_id="ws", name="svc")
    r._deployments["dep"] = Deployment(id="dep", workspace_id="ws", service_id="svc", version="v1")
    r._provider_bindings["bind"] = ServiceProviderBinding(id="bind", workspace_id="ws", service_id="svc", provider="render", provider_service_id="render-svc")
    return r

def test_reconciliation_persists_attempt_and_terminal_verification():
    r=setup_repo(); r._deployment_outcomes["o"] = __import__('plugins.industries.it_operations.models', fromlist=['DeploymentOutcome']).DeploymentOutcome(id="o", workspace_id="ws", deployment_id="dep", status="uncertain", provider_reference="d1")
    out=ITDeploymentReconciliationService(r, FakeRender("succeeded")).reconcile(workspace_id="ws",service_id="svc",deployment_id="dep",provider_reference="d1")
    assert out["terminal"] is True
    ev=r.list_reconciliation_evidence("ws","dep")
    assert len(ev)==1 and ev[0]["attempt_number"]==1
    assert ev[0]["verification_state"]=="verified_provider_terminal"

def test_nonterminal_attempt_is_durable_and_remains_reconcilable():
    r=setup_repo(); r._deployment_outcomes["o"] = __import__('plugins.industries.it_operations.models', fromlist=['DeploymentOutcome']).DeploymentOutcome(id="o", workspace_id="ws", deployment_id="dep", status="uncertain", provider_reference="d1")
    out=ITDeploymentReconciliationService(r, FakeRender("in_progress")).reconcile(workspace_id="ws",service_id="svc",deployment_id="dep",provider_reference="d1")
    assert out["reconciliation_required"] is True
    assert r.list_reconciliation_evidence("ws","dep")[0]["verification_state"]=="provider_observed_nonterminal"

def test_evidence_read_model_is_read_only_projection():
    r=setup_repo()
    payload=build_it_deployment_evidence(r, workspace_id="ws", deployment_id="dep")
    assert payload["readOnly"] is True
    assert payload["verification"]["providerStateIsBusinessOutcome"] is False

def test_reconciliation_budget_moves_to_operator_review_without_deployment_retry():
    r=setup_repo(); r._deployment_outcomes["o"] = __import__('plugins.industries.it_operations.models', fromlist=['DeploymentOutcome']).DeploymentOutcome(id="o", workspace_id="ws", deployment_id="dep", status="uncertain", provider_reference="d1")
    r.record_reconciliation_evidence("ws","dep","svc","render","render-svc","d1",1,"in_progress",False,{"status":"in_progress"},"provider_observed_nonterminal")
    orch=ReconciliationOrchestrator(ReconciliationPolicy(max_attempts=1))
    fake=FakeRender("succeeded")
    result=orch.reconcile_it_deployments(repository=r, render_adapter=fake)
    assert result["uncertain"] == 1
    assert fake.calls == 0
    latest=r.list_reconciliation_evidence("ws","dep")[0]
    assert latest["verification_state"] == "operator_review"
