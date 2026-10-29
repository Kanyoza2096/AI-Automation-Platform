from services.reconciliation_orchestrator import ReconciliationOrchestrator
from plugins.industries.it_operations.repository import ITOperationsRepository


class FakeRender:
    def __init__(self, observed):
        self.observed = observed
        self.reconcile_calls = []
        self.trigger_calls = 0

    def reconcile_deploy(self, service_id, deploy_id):
        self.reconcile_calls.append((service_id, deploy_id))
        return dict(self.observed)

    def trigger_deploy(self, *args, **kwargs):
        self.trigger_calls += 1
        raise AssertionError("orchestration must never trigger a deployment")


def _setup():
    repo = ITOperationsRepository()
    service = repo.add_service("tenant-a", {"name": "api", "environment": "production"})
    repo.bind_service_provider("tenant-a", service.id, {
        "provider": "render", "provider_service_id": "srv-123", "environment": "production"
    })
    deployment = repo.request_deployment("tenant-a", service.id, {
        "version": "2026.10.02.1", "environment": "production"
    })
    return repo, service, deployment


def test_orchestrator_routes_it_terminal_reconciliation():
    repo, service, deployment = _setup()
    render = FakeRender({"status": "succeeded", "deploy_id": "dep-1", "raw_status": "live"})

    result = ReconciliationOrchestrator().reconcile_it_deployment(
        repository=repo,
        render_adapter=render,
        workspace_id="tenant-a",
        service_id=service.id,
        deployment_id=deployment.id,
        provider_reference="dep-1",
    )

    assert result["status"] == "succeeded"
    assert result["terminal"] is True
    assert render.reconcile_calls == [("srv-123", "dep-1")]
    assert render.trigger_calls == 0


def test_orchestrator_preserves_non_terminal_it_reconciliation():
    repo, service, deployment = _setup()
    render = FakeRender({"status": "in_progress", "deploy_id": "dep-1", "raw_status": "building"})

    result = ReconciliationOrchestrator().reconcile_it_deployment(
        repository=repo,
        render_adapter=render,
        workspace_id="tenant-a",
        service_id=service.id,
        deployment_id=deployment.id,
        provider_reference="dep-1",
    )

    assert result["status"] == "in_progress"
    assert result["terminal"] is False
    assert result["reconciliation_required"] is True
    assert not repo._deployment_outcomes
    assert render.trigger_calls == 0
