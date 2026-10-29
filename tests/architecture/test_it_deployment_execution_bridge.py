import asyncio

from services.action_kernel import build_default_action_kernel
from services.sector_it_deployment_execution import register_it_deployment_action
from plugins.industries.it_operations.repository import ITOperationsRepository


class FakeRender:
    def __init__(self):
        self.calls = []

    def trigger_deploy(self, service_id, **kwargs):
        self.calls.append((service_id, kwargs))
        return {
            "ok": True,
            "outcome": "uncertain",
            "provider": "render",
            "provider_object_id": "dep-render-1",
            "request_id": "req-1",
            "metadata": {"reconciliation_required": True},
        }

    def reconcile_deploy(self, service_id, deploy_id):
        return {"provider": "render", "status": "succeeded", "service_id": service_id, "deploy_id": deploy_id}


def test_it_request_deployment_is_dependency_injected_and_uncertain_until_reconciled():
    repo = ITOperationsRepository()
    service = repo.add_service("tenant-a", {"name": "api", "environment": "production"})
    repo.bind_service_provider("tenant-a", service.id, {
        "provider": "render", "provider_service_id": "srv-123", "environment": "production"
    })
    repo.bind_deployment_version("tenant-a", service.id, {
        "provider": "render", "version": "2026.10.02.1", "provider_version_ref": "abc123"
    })
    render = FakeRender()
    kernel = build_default_action_kernel(allow_in_memory=True, it_repository=repo, render_adapter=render)

    result = asyncio.run(kernel.execute(
        workspace_id="tenant-a",
        action_type="it_request_deployment",
        parameters={"service_id": service.id, "version": "2026.10.02.1", "environment": "production"},
        risk_level="critical",
        decision_id="decision-1",
        source="test",
    ))

    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True
    assert result["output"]["provider_object_id"] == "dep-render-1"
    assert render.calls[0][0] == "srv-123"
    assert render.calls[0][1]["commit_id"] == "abc123"


def test_it_request_deployment_requires_explicit_provider_identity_and_version_reference():
    repo = ITOperationsRepository()
    service = repo.add_service("tenant-a", {"name": "api", "environment": "production"})
    render = FakeRender()
    kernel = build_default_action_kernel(allow_in_memory=True, it_repository=repo, render_adapter=render)

    result = asyncio.run(kernel.execute(
        workspace_id="tenant-a",
        action_type="it_request_deployment",
        parameters={"service_id": service.id, "version": "2026.10.02.1", "environment": "production"},
        risk_level="critical", decision_id="decision-1", source="test",
    ))
    assert result["status"] == "uncertain"
    assert "Render service binding" in str(result["output"].get("error", ""))
    assert render.calls == []
