from services.sector_it_deployment_reconciliation import ITDeploymentReconciliationService
from plugins.industries.it_operations.repository import ITOperationsRepository


class FakeRender:
    def __init__(self, observed):
        self.observed = observed
        self.calls = []

    def reconcile_deploy(self, service_id, deploy_id):
        self.calls.append((service_id, deploy_id))
        return dict(self.observed)


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


def test_succeeded_reconciliation_persists_terminal_outcome():
    repo, service, deployment = _setup()
    render = FakeRender({"status": "succeeded", "deploy_id": "dep-render-1", "raw_status": "live"})
    reconciler = ITDeploymentReconciliationService(repo, render)

    result = reconciler.reconcile(
        workspace_id="tenant-a", service_id=service.id,
        deployment_id=deployment.id, provider_reference="dep-render-1",
    )

    assert result["status"] == "succeeded"
    assert result["terminal"] is True
    assert result["reconciliation_required"] is False
    assert result["outcome_id"]
    assert render.calls == [("srv-123", "dep-render-1")]


def test_failed_reconciliation_persists_terminal_outcome():
    repo, service, deployment = _setup()
    render = FakeRender({"status": "failed", "deploy_id": "dep-render-1", "raw_status": "build_failed"})
    reconciler = ITDeploymentReconciliationService(repo, render)

    result = reconciler.reconcile(
        workspace_id="tenant-a", service_id=service.id,
        deployment_id=deployment.id, provider_reference="dep-render-1",
    )

    assert result["status"] == "failed"
    assert result["terminal"] is True
    assert result["reconciliation_required"] is False


def test_in_progress_does_not_finalize_deployment():
    repo, service, deployment = _setup()
    render = FakeRender({"status": "in_progress", "deploy_id": "dep-render-1", "raw_status": "building"})
    reconciler = ITDeploymentReconciliationService(repo, render)

    result = reconciler.reconcile(
        workspace_id="tenant-a", service_id=service.id,
        deployment_id=deployment.id, provider_reference="dep-render-1",
    )

    assert result["status"] == "in_progress"
    assert result["terminal"] is False
    assert result["reconciliation_required"] is True
    assert not repo._deployment_outcomes


def test_wrong_service_identity_fails_closed():
    repo, service, deployment = _setup()
    other = repo.add_service("tenant-a", {"name": "worker", "environment": "production"})
    repo.bind_service_provider("tenant-a", other.id, {
        "provider": "render", "provider_service_id": "srv-other", "environment": "production"
    })
    render = FakeRender({"status": "succeeded", "deploy_id": "dep-render-1"})
    reconciler = ITDeploymentReconciliationService(repo, render)

    try:
        reconciler.reconcile(
            workspace_id="tenant-a", service_id=other.id,
            deployment_id=deployment.id, provider_reference="dep-render-1",
        )
        assert False, "expected service mismatch to fail"
    except ValueError as exc:
        assert "does not belong" in str(exc)
    assert render.calls == []


def test_reconciliation_never_triggers_another_deployment():
    repo, service, deployment = _setup()
    render = FakeRender({"status": "succeeded", "deploy_id": "dep-render-1"})
    render.trigger_calls = 0
    render.trigger_deploy = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not deploy"))
    reconciler = ITDeploymentReconciliationService(repo, render)

    result = reconciler.reconcile(
        workspace_id="tenant-a", service_id=service.id,
        deployment_id=deployment.id, provider_reference="dep-render-1",
    )
    assert result["status"] == "succeeded"
