from datetime import datetime, timezone
import httpx
import pytest

from services.connectors.enterprise_registry import create_enterprise_adapter, supported_enterprise_providers
from services.connectors.github_adapter import GitHubAdapter
from services.connectors.render_adapter import RenderAdapter


def make_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_registry_exposes_curated_enterprise_provider_fabric_without_social_factory_coupling():
    assert supported_enterprise_providers() == ("github", "render", "stripe", "shopify", "slack", "jira", "pagerduty", "datadog", "cloudflare", "twilio", "google_workspace", "aws")
    assert isinstance(create_enterprise_adapter("github", token="test", base_url="http://testserver"), GitHubAdapter)
    assert isinstance(create_enterprise_adapter("render", token="test", base_url="http://testserver"), RenderAdapter)
    assert create_enterprise_adapter("stripe", token="test") is not None


def test_github_read_operations_send_bearer_and_api_version_headers():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer secret-token"
        assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
        return httpx.Response(200, json={"full_name": "acme/app", "id": 10})
    adapter = GitHubAdapter(token="secret-token", client=make_client(handler))
    assert adapter.get_repository("acme", "app")["full_name"] == "acme/app"


def test_github_issue_listing_filters_out_pull_requests():
    def handler(request):
        assert request.url.params["state"] == "open"
        return httpx.Response(200, json=[{"id": 1, "number": 2}, {"id": 2, "number": 3, "pull_request": {}}])
    adapter = GitHubAdapter(token="x", client=make_client(handler))
    assert [x["number"] for x in adapter.list_issues("acme", "app")] == [2]


def test_mutations_are_disabled_by_default():
    adapter = GitHubAdapter(token="x", client=make_client(lambda r: httpx.Response(201, json={"id": 7})))
    with pytest.raises(PermissionError): adapter.create_issue("acme", "app", title="Bug")


def test_github_create_issue_returns_authoritative_provider_receipt():
    def handler(request):
        assert request.method == "POST"
        assert request.url.path.endswith("/repos/acme/app/issues")
        assert request.read()
        return httpx.Response(201, headers={"X-GitHub-Request-Id": "req-1"}, json={"id": 501, "number": 12, "html_url": "https://github.com/acme/app/issues/12"})
    adapter = GitHubAdapter(token="x", client=make_client(handler), allow_mutations=True)
    result = adapter.create_issue("acme", "app", title="Bug")
    assert result["status"] == "succeeded" and result["provider_object_id"] == "501"
    assert result["request_id"] == "req-1" and result["metadata"]["issue_number"] == 12


def test_github_comment_requires_nonempty_body_and_returns_comment_id():
    adapter = GitHubAdapter(token="x", client=make_client(lambda r: httpx.Response(201, json={"id": 88, "html_url": "https://example/comment"})), allow_mutations=True)
    with pytest.raises(ValueError): adapter.add_issue_comment("acme", "app", 1, body=" ")
    result = adapter.add_issue_comment("acme", "app", 1, body="Investigating")
    assert result["status"] == "succeeded" and result["provider_object_id"] == "88"


def test_high_risk_github_workflow_dispatch_requires_two_explicit_gates():
    adapter = GitHubAdapter(token="x", client=make_client(lambda r: httpx.Response(204)), allow_mutations=True)
    with pytest.raises(PermissionError): adapter.dispatch_workflow("acme", "app", workflow_id="ci.yml", ref="main")
    adapter.allow_high_risk = True
    with pytest.raises(PermissionError): adapter.dispatch_workflow("acme", "app", workflow_id="ci.yml", ref="main")


def test_workflow_dispatch_without_run_id_is_uncertain_not_success():
    adapter = GitHubAdapter(token="x", client=make_client(lambda r: httpx.Response(204, headers={"X-GitHub-Request-Id": "dispatch-1"})), allow_mutations=True, allow_high_risk=True)
    result = adapter.dispatch_workflow("acme", "app", workflow_id="ci.yml", ref="main", confirm=True)
    assert result["status"] == "uncertain" and result["provider_object_id"] is None
    assert result["metadata"]["reconciliation_required"] is True


def test_ambiguous_github_server_error_requires_reconciliation():
    adapter = GitHubAdapter(token="x", client=make_client(lambda r: httpx.Response(503, json={"message": "temporarily unavailable"})), allow_mutations=True)
    result = adapter.create_issue("acme", "app", title="Bug")
    assert result["status"] == "uncertain"
    assert result["metadata"]["reconciliation_required"] is True


def test_github_transport_timeout_on_mutation_is_ambiguous():
    def handler(request): raise httpx.ReadTimeout("simulated timeout")
    adapter = GitHubAdapter(token="x", client=make_client(handler), allow_mutations=True)
    result = adapter.create_issue("acme", "app", title="Bug")
    assert result["status"] == "uncertain" and result["metadata"]["reconciliation_required"] is True


def test_workflow_reconciliation_never_claims_authoritative_absence():
    def handler(request):
        return httpx.Response(200, json={"workflow_runs": []})
    adapter = GitHubAdapter(token="x", client=make_client(handler))
    result = adapter.reconcile_workflow("acme", "app", workflow_id="ci.yml", ref="main", started_after=datetime.now(timezone.utc))
    assert result["status"] == "inconclusive" and result["authoritative_absence"] is False


def test_render_service_and_deploy_reads():
    def handler(request):
        if request.url.path.endswith("/services/srv-1/deploys"):
            return httpx.Response(200, json=[{"id": "dep-1", "status": "live"}])
        if request.url.path.endswith("/services/srv-1"):
            return httpx.Response(200, json={"id": "srv-1", "name": "api"})
        return httpx.Response(200, json={"id": "dep-1", "status": "live", "serviceId": "srv-1"})
    adapter = RenderAdapter(token="x", client=make_client(handler))
    assert adapter.get_service("srv-1")["name"] == "api"
    assert adapter.list_deploys("srv-1")[0]["id"] == "dep-1"
    assert adapter.get_deploy("dep-1")["status"] == "live"


def test_render_deploy_requires_explicit_high_risk_confirmation():
    adapter = RenderAdapter(token="x", client=make_client(lambda r: httpx.Response(201, json={"id": "dep-1", "status": "build_in_progress"})), allow_mutations=True)
    with pytest.raises(PermissionError): adapter.trigger_deploy("srv-1", confirm=True)
    adapter.allow_high_risk = True
    with pytest.raises(PermissionError): adapter.trigger_deploy("srv-1")


def test_render_trigger_deploy_records_deploy_id_but_not_false_success():
    def handler(request):
        assert request.method == "POST" and request.url.path.endswith("/services/srv-1/deploys")
        return httpx.Response(201, json={"id": "dep-123", "status": "build_in_progress"})
    adapter = RenderAdapter(token="x", client=make_client(handler), allow_mutations=True, allow_high_risk=True)
    result = adapter.trigger_deploy("srv-1", commit_id="abc123", confirm=True)
    assert result["status"] == "uncertain" and result["observed_object_id"] == "dep-123"
    assert result["provider_object_id"] is None and result["metadata"]["reconciliation_required"] is True


def test_render_reconciliation_reads_state_and_checks_service_ownership():
    adapter = RenderAdapter(token="x", client=make_client(lambda r: httpx.Response(200, json={"id": "dep-1", "status": "live", "serviceId": "srv-1"})))
    result = adapter.reconcile_deploy("srv-1", "dep-1")
    assert result["status"] == "succeeded" and result["provider_object_id"] == "dep-1"
    with pytest.raises(ValueError): adapter.reconcile_deploy("srv-other", "dep-1")


def test_render_cancel_is_uncertain_until_deploy_state_is_verified():
    adapter = RenderAdapter(token="x", client=make_client(lambda r: httpx.Response(202)), allow_mutations=True, allow_high_risk=True)
    result = adapter.cancel_deploy("dep-1", confirm=True)
    assert result["status"] == "uncertain" and result["observed_object_id"] == "dep-1"


def test_mutation_http_error_does_not_leak_bearer_token():
    adapter = GitHubAdapter(token="do-not-leak", client=make_client(lambda r: httpx.Response(403, json={"message": "Resource not accessible"})), allow_mutations=True)
    result = adapter.create_issue("acme", "app", title="Bug")
    assert result["status"] == "failed"
    assert "do-not-leak" not in result["error"]


def test_enterprise_mutations_register_as_approval_gated_kernel_contracts():
    from services.action_kernel import ActionKernel
    from services.connectors.enterprise_action_contracts import register_github_actions, register_render_actions
    kernel = ActionKernel(allow_in_memory=True)
    github = GitHubAdapter(token="x", client=make_client(lambda r: httpx.Response(204)), allow_mutations=True, allow_high_risk=True)
    render = RenderAdapter(token="x", client=make_client(lambda r: httpx.Response(204)), allow_mutations=True, allow_high_risk=True)
    github_names = register_github_actions(kernel, github)
    render_names = register_render_actions(kernel, render)
    assert "github.merge_pull_request" in github_names
    assert "github.dispatch_workflow" in github_names
    assert "render.trigger_deploy" in render_names
    assert all(kernel._contracts[name].requires_approval for name in github_names + render_names)
    assert all(kernel._contracts[name].irreversible for name in ("github.dispatch_workflow", "github.merge_pull_request", "render.trigger_deploy"))


def test_render_production_deploy_requires_separate_production_confirmation():
    adapter = RenderAdapter(token="x", client=make_client(lambda r: httpx.Response(201, json={"id": "dep-1"})), allow_mutations=True, allow_high_risk=True)
    with pytest.raises(PermissionError, match="confirm_production"):
        adapter.trigger_deploy("srv-1", confirm=True, production=True)
    result = adapter.trigger_deploy("srv-1", confirm=True, production=True, confirm_production=True)
    assert result["status"] == "uncertain"


def test_adapter_results_obey_canonical_provider_receipt_protocol():
    from services.receipt_protocol import receipt_from_result
    github = GitHubAdapter(token="x", client=make_client(lambda r: httpx.Response(201, json={"id": 100, "number": 8})), allow_mutations=True)
    success = receipt_from_result(github.create_issue("acme", "app", title="Incident"), provider="github")
    assert success.authoritative and success.provider_object_id == "100"

    render = RenderAdapter(token="x", client=make_client(lambda r: httpx.Response(201, json={"id": "dep-9", "status": "build_in_progress"})), allow_mutations=True, allow_high_risk=True)
    uncertain = receipt_from_result(render.trigger_deploy("srv-9", confirm=True), provider="render")
    assert uncertain.status == "uncertain" and uncertain.provider_object_id is None
    assert uncertain.observed_object_id == "dep-9"


def test_render_build_failure_reconciles_as_failed_not_success():
    adapter = RenderAdapter(token="x", client=make_client(lambda r: httpx.Response(200, json={"id": "dep-1", "status": "build_failed", "serviceId": "srv-1"})))
    result = adapter.reconcile_deploy("srv-1", "dep-1")
    assert result["status"] == "failed" and result["provider_object_id"] is None
