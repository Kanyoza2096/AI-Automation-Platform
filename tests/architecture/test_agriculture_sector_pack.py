from core.request_context import RequestContext, clear_request_context, set_request_context
from plugins.industries.agriculture.plugin import AgriculturePlugin


def test_agriculture_manifest_is_formal():
    manifest = AgriculturePlugin(None, None).get_manifest()
    assert manifest.pack_id == "agriculture"
    assert "crop_cycles" in manifest.resources
    assert "agriculture.request_input_delivery" in manifest.external_actions
    assert "agriculture.crop_cycle_authorization" in manifest.policies


def test_commands_declare_governed_execution():
    commands = {x["intent"]: x for x in AgriculturePlugin(None, None).get_commands()}
    assert commands["get_farm"]["execution_mode"] == "read_only"
    assert commands["create_crop_cycle"]["execution_mode"] == "action_kernel"
    assert commands["request_input_delivery"]["governed_action_type"] == "agriculture_request_input_delivery"


def test_workspace_is_authoritative_and_cross_tenant_reads_fail():
    plugin = AgriculturePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="farm-a", role="owner"))
    try:
        farm = plugin.execute("create_farm", {"workspace_id": "farm-b", "name": "North Farm", "farm_type": "mixed"}, "farm-b")["data"]["id"]
        assert plugin.execute("get_farm", {"farm_id": farm}, "farm-b")["ok"] is True
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="farm-b", role="owner"))
        assert plugin.execute("get_farm", {"farm_id": farm}, "farm-b")["ok"] is False
    finally:
        clear_request_context()


def test_field_crop_activity_and_harvest_flow():
    plugin = AgriculturePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="farm-a", role="owner"))
    try:
        farm = plugin.execute("create_farm", {"name": "North Farm", "farm_type": "crop"}, "farm-a")["data"]["id"]
        field = plugin.execute("create_field", {"farm_id": farm, "name": "Field 1", "area": 12.5}, "farm-a")["data"]["id"]
        cycle = plugin.execute("create_crop_cycle", {"field_id": field, "crop": "maize", "season": "2026-main"}, "farm-a")["data"]["id"]
        assert plugin.execute("update_crop_cycle", {"crop_cycle_id": cycle, "status": "planted"}, "farm-a")["data"]["status"] == "planted"
        activity = plugin.execute("record_activity", {"field_id": field, "activity_type": "fertilize", "notes": "First application"}, "farm-a")
        assert activity["ok"] is True
        harvest = plugin.execute("record_harvest", {"crop_cycle_id": cycle, "quantity": 1250, "unit": "kg", "quality_grade": "premium"}, "farm-a")
        assert harvest["ok"] is True
    finally:
        clear_request_context()


def test_invalid_field_rejected():
    plugin = AgriculturePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="farm-a", role="owner"))
    try:
        result = plugin.execute("create_crop_cycle", {"field_id": "missing", "crop": "maize", "season": "2026"}, "farm-a")
        assert result["ok"] is False
    finally:
        clear_request_context()


def test_external_agriculture_actions_are_connector_deferred():
    plugin = AgriculturePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="farm-a", role="owner"))
    try:
        dispatch = plugin.execute("dispatch_field_task", {"field_id": "field-1", "task": "spray"}, "farm-a")
        delivery = plugin.execute("request_input_delivery", {"input_type": "fertilizer", "quantity": 100}, "farm-a")
        assert dispatch["status"] == "action_required"
        assert dispatch["action_type"] == "agriculture_dispatch_field_task"
        assert "connector-deferred" in dispatch["message"]
        assert delivery["action_type"] == "agriculture_request_input_delivery"
    finally:
        clear_request_context()
