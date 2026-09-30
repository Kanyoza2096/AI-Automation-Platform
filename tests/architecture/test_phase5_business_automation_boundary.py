from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from application.business_automation import (
    BusinessAutomationApplicationService,
    BusinessAutomationContext,
)


class FakeAutomationPort:
    def __init__(self):
        self.calls = []

    async def create_rule(self, workspace_id, payload):
        self.calls.append(("create", workspace_id, payload))
        return SimpleNamespace(id="r1", workspace_id=workspace_id)

    async def update_rule(self, workspace_id, rule_id, payload):
        self.calls.append(("update", workspace_id, rule_id, payload))
        return SimpleNamespace(id=rule_id, workspace_id=workspace_id)

    async def list_rules(self, workspace_id):
        self.calls.append(("list", workspace_id))
        return []

    async def run(self, workspace_id, *, event, rule_id=None, dry_run=True, approved=False):
        self.calls.append(("run", workspace_id, event, rule_id, dry_run, approved))
        return []

    async def history(self, workspace_id, limit=50):
        self.calls.append(("history", workspace_id, limit))
        return []


@pytest.mark.asyncio
async def test_business_automation_application_service_enforces_authenticated_workspace_context():
    port = FakeAutomationPort()
    service = BusinessAutomationApplicationService(port)

    await service.create_rule(BusinessAutomationContext(" ws-1 "), {"action": "log"})
    await service.run(
        BusinessAutomationContext("ws-1"),
        event={"name": "lead.created"},
        rule_id=None,
        dry_run=True,
        approved=False,
    )

    assert port.calls[0][1] == "ws-1"
    assert port.calls[1][1] == "ws-1"


def test_business_automation_application_layer_has_no_fastapi_dependency():
    source = inspect.getsource(BusinessAutomationApplicationService)
    assert "fastapi" not in source.lower()
    assert "Request" not in source


def test_business_automation_route_uses_application_service_for_protected_use_cases():
    from api import content_intelligence_automation as module

    source = inspect.getsource(module)
    assert "BusinessAutomationApplicationService" in source
    assert "_business_automation_application" in source
    assert "business_automation_engine.create_rule" not in source
    assert "business_automation_engine.update_rule" not in source
    assert "business_automation_engine.run(" not in source
    assert "business_automation_engine.history" not in source


def test_empty_workspace_context_is_rejected_before_port_call():
    context = BusinessAutomationContext("   ")
    with pytest.raises(ValueError, match="workspace scope"):
        context.validate()
