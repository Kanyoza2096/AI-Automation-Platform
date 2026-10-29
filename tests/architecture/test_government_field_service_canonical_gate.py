import asyncio

from services.action_kernel import build_default_action_kernel
from services.reconciliation_orchestrator import ReconciliationOrchestrator
from plugins.industries.government.repository import GovernmentRepository


class FakeJira:
    def __init__(self):
        self.created = []
        self.issue = {"id": "9001", "key": "GOV-77", "fields": {"summary": "Field service"}}

    def create_issue(self, *, project_key, issue_type, summary, description=""):
        self.created.append((project_key, issue_type, summary, description))
        return {"status": "succeeded", "object_id": "9001", "request_id": "req-77", "metadata": {"issue_key": "GOV-77"}}

    def get_issue(self, issue_key):
        assert issue_key == "GOV-77"
        return self.issue


def test_government_field_service_execution_is_uncertain_until_reconciled():
    repo = GovernmentRepository()
    jira = FakeJira()
    service = repo.create_service("gov-ws", {"name": "Field Services", "category": "municipal"})
    request = repo.create_request("gov-ws", {"service_id": service.id, "title": "Inspect blocked drain", "priority": "high"})
    repo.bind_field_service_provider("gov-ws", "jira", {"project_key": "GOV", "issue_type": "Task"})
    kernel = build_default_action_kernel(allow_in_memory=True, government_repository=repo, jira_adapter=jira)

    result = asyncio.run(kernel.execute(
        workspace_id="gov-ws",
        action_type="government_dispatch_field_service",
        parameters={"request_id": request.id, "team_id": "team-7"},
        risk_level="high",
        decision_id="decision-gov-field-1",
        source="test",
    ))
    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True
    assert jira.created
    assert result["output"]["provider_receipt_is_business_outcome"] is False

    dispatch_id = result["output"]["dispatch_id"]
    reconciled = ReconciliationOrchestrator().reconcile_government_field_service(
        repository=repo, jira_adapter=jira, workspace_id="gov-ws", dispatch_id=dispatch_id
    )
    assert reconciled["status"] == "succeeded"
    assert reconciled["terminal"] is True
    assert len(jira.created) == 1


def test_missing_field_service_binding_fails_closed_before_provider_mutation():
    repo = GovernmentRepository()
    jira = FakeJira()
    service = repo.create_service("gov-ws", {"name": "Field Services", "category": "municipal"})
    request = repo.create_request("gov-ws", {"service_id": service.id, "title": "Inspect drain"})
    kernel = build_default_action_kernel(allow_in_memory=True, government_repository=repo, jira_adapter=jira)
    result = asyncio.run(kernel.execute(
        workspace_id="gov-ws",
        action_type="government_dispatch_field_service",
        parameters={"request_id": request.id, "team_id": "team-7"},
        risk_level="high",
        decision_id="decision-gov-field-2",
        source="test",
    ))
    assert result["status"] == "uncertain"
    assert jira.created == []


def test_field_service_reconciliation_never_dispatches_again():
    repo = GovernmentRepository()
    jira = FakeJira()
    service = repo.create_service("gov-ws", {"name": "Field Services", "category": "municipal"})
    request = repo.create_request("gov-ws", {"service_id": service.id, "title": "Inspect drain"})
    repo.bind_field_service_provider("gov-ws", "jira", {"project_key": "GOV", "issue_type": "Task"})
    kernel = build_default_action_kernel(allow_in_memory=True, government_repository=repo, jira_adapter=jira)
    result = asyncio.run(kernel.execute(
        workspace_id="gov-ws", action_type="government_dispatch_field_service",
        parameters={"request_id": request.id, "team_id": "team-7"}, risk_level="high",
        decision_id="decision-gov-field-3", source="test",
    ))
    dispatch_id = result["output"]["dispatch_id"]
    ReconciliationOrchestrator().reconcile_government_field_service(repository=repo, jira_adapter=jira, workspace_id="gov-ws", dispatch_id=dispatch_id)
    ReconciliationOrchestrator().reconcile_government_field_service(repository=repo, jira_adapter=jira, workspace_id="gov-ws", dispatch_id=dispatch_id)
    assert len(jira.created) == 1
