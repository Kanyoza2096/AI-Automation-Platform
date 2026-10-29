from plugins.industries.crm.plugin import CRMPlugin
from core.request_context import RequestContext, clear_request_context, set_request_context


def test_crm_manifest_is_formal_and_explicit():
    manifest = CRMPlugin(None, None).get_manifest()
    assert manifest.pack_id == "crm"
    assert manifest.version == "1.1"
    assert "contacts" in manifest.resources
    assert "crm.send_message" in manifest.external_actions
    assert "deal.updated" in manifest.events


def test_crm_commands_declare_execution_semantics():
    commands = {item["intent"]: item for item in CRMPlugin(None, None).get_commands()}
    assert commands["get_contacts"]["execution_mode"] == "read_only"
    assert commands["add_contact"]["execution_mode"] == "action_kernel"
    assert commands["update_deal_stage"]["governed_action_type"] == "crm_update_deal_stage"
    assert commands["send_message"]["governed_action_type"] == "crm_send_message"


def test_crm_workspace_is_authenticated_context_not_client_input():
    plugin = CRMPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="tenant-a", role="owner"))
    try:
        result = plugin.execute("add_contact", {"workspace_id": "tenant-b", "name": "Alice"}, "tenant-b")
        assert result["ok"] is True
        contact_id = result["data"]["contact_id"]
        stored = plugin.execute("get_contacts", {"status": "lead"}, "tenant-a")
        assert stored["ok"] and any(x["id"] == contact_id for x in stored["data"]["contacts"])
    finally:
        clear_request_context()


def test_crm_external_communication_is_connector_deferred():
    plugin = CRMPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="tenant-a", role="owner"))
    try:
        plugin.execute("add_contact", {"name": "Alice", "phone": "0999000000"}, "tenant-a")
        result = plugin.execute("send_message", {"message": "Hello", "status": "lead"}, "tenant-a")
        assert result["status"] == "action_required"
        assert result["execution_mode"] == "action_kernel"
        assert result["action_type"] == "crm_send_message"
        assert "connector layer" in result["message"]
    finally:
        clear_request_context()


def test_crm_contact_and_deal_flow_with_workspace_isolation():
    plugin = CRMPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="tenant-a", role="owner"))
    try:
        contact = plugin.execute("add_contact", {"name": "Jane Doe", "company": "Acme"}, "tenant-a")["data"]
        deal = plugin.execute("add_deal", {"amount": 5000, "contact": "Jane Doe", "stage": "lead"}, "tenant-a")["data"]
        assert deal["amount"] == 5000
        contact_update = plugin.execute("update_contact_status", {"contact_id": contact["contact_id"], "status": "qualified"}, "tenant-a")
        assert contact_update["ok"] is True and contact_update["data"]["status"] == "qualified"
        updated = plugin.execute("update_deal_stage", {"deal_id": deal["deal_id"], "stage": "negotiation"}, "tenant-a")
        assert updated["ok"] is True and updated["data"]["stage"] == "negotiation"
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="tenant-b", role="owner"))
        assert plugin.execute("get_contacts", {"status": "lead"}, "tenant-b")["data"]["contacts"] == []
        assert plugin.execute("update_deal_stage", {"deal_id": deal["deal_id"], "stage": "won"}, "tenant-b")["ok"] is False
    finally:
        clear_request_context()
