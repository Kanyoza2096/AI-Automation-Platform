from datetime import datetime, timezone

from plugins.industries.it_operations.models import Deployment, DeploymentOutcome, Service
from plugins.industries.it_operations.repository import ITOperationsRepository
from services.reconciliation_orchestrator import ReconciliationOrchestrator


class FakeRender:
    def __init__(self, status="live"):
        self.status = status
        self.calls = []

    def reconcile_deploy(self, service_id, deploy_id):
        self.calls.append((service_id, deploy_id))
        return {"status": self.status, "deploy_id": deploy_id}



def _repo_with_uncertain():
    repo = ITOperationsRepository()
    repo._services["svc"] = Service(id="svc", workspace_id="ws", name="api")
    repo._deployments["dep"] = Deployment(id="dep", workspace_id="ws", service_id="svc", version="v1")
    repo._deployment_outcomes["out"] = DeploymentOutcome(
        id="out", workspace_id="ws", deployment_id="dep", status="uncertain",
        provider_reference="dpl-1", observed_at=datetime.now(timezone.utc)
    )
    repo.bind_service_provider("ws", "svc", {"provider": "render", "provider_service_id": "render-svc", "environment": "production"})
    return repo


def test_cycle_discovers_durable_uncertain_deployment_and_reconciles_without_redeploying():
    repo = _repo_with_uncertain()
    render = FakeRender("succeeded")

    stats = ReconciliationOrchestrator().reconcile_it_deployments(
        repository=repo, render_adapter=render, workspace_id="ws", limit=10
    )

    assert stats == {"discovered": 1, "reconciled": 1, "terminal": 1, "in_progress": 0, "uncertain": 0, "skipped": 0}
    assert render.calls == [("render-svc", "dpl-1")]
    outcomes = list(repo._deployment_outcomes.values())
    assert any(o.status == "succeeded" and o.deployment_id == "dep" for o in outcomes)
    assert not any(getattr(o, "status", None) == "requested" for o in outcomes)


def test_cycle_preserves_nonterminal_provider_state_for_next_scheduled_pass():
    repo = _repo_with_uncertain()
    render = FakeRender("building")

    stats = ReconciliationOrchestrator().reconcile_it_deployments(
        repository=repo, render_adapter=render, workspace_id="ws"
    )

    assert stats["in_progress"] == 1
    assert stats["terminal"] == 0
    assert render.calls == [("render-svc", "dpl-1")]
    assert not any(o.status == "succeeded" for o in repo._deployment_outcomes.values())


def test_cycle_is_workspace_scoped_and_skips_missing_provider_identity():
    repo = _repo_with_uncertain()
    repo._deployment_outcomes["out2"] = DeploymentOutcome(
        id="out2", workspace_id="ws", deployment_id="dep", status="uncertain",
        provider_reference=None, observed_at=datetime.now(timezone.utc)
    )
    render = FakeRender("succeeded")

    stats = ReconciliationOrchestrator().reconcile_it_deployments(
        repository=repo, render_adapter=render, workspace_id="ws", limit=1
    )

    assert stats["discovered"] == 1
    assert stats["skipped"] == 1
    assert render.calls == []
