import pytest
from services.business_automation import BusinessAutomationEngine
from services.action_kernel import build_default_action_kernel
from plugins.industries.crm.repository import CRMRepository

@pytest.mark.asyncio
async def test_state_actions_are_registered_and_governed():
    e = BusinessAutomationEngine()
    assert "crm_update_contact_status" in e._actions
    assert "crm_update_deal_stage" in e._actions
    k = build_default_action_kernel(allow_in_memory=True)
    assert "crm_update_contact_status" in k._contracts
    assert "crm_update_deal_stage" in k._contracts

@pytest.mark.asyncio
async def test_contact_status_transition_emits_lifecycle_event(monkeypatch):
    repo = CRMRepository()
    c = repo.add_contact("ws1", {"contact_name": "Jane", "status": "lead"})
    e = BusinessAutomationEngine()
    monkeypatch.setattr(e, "_crm_plugin", lambda ws: (type("P", (), {"execute": lambda self, cmd, payload, workspace_id: {"ok": True, "data": {"contact_id": c.id, "name": "Jane", "status": "qualified"}}})(), ws))
    class Bus:
        async def publish(self, *args, **kwargs): return "evt-status-1"
    import core.unified_bus
    async def get_bus(): return Bus()
    monkeypatch.setattr(core.unified_bus, "get_unified_bus", get_bus)
    # exercise only lifecycle wrapper; kernel remains the authoritative boundary
    result = await e._action_crm_update_contact_status({"event_id": "lead-1"}, {"contact_id": c.id, "status": "qualified"}, workspace_id="ws1", idempotency_key="k1", approved=True)
    assert result["status"] == "succeeded"
    assert result["lifecycle_event"]["event_name"] == "crm.contact.status_changed"
