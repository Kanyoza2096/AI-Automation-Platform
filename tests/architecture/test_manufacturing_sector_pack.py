from core.request_context import RequestContext, clear_request_context, set_request_context
from plugins.industries.manufacturing.plugin import ManufacturingPlugin


def test_manufacturing_manifest_is_formal():
    manifest = ManufacturingPlugin(None, None).get_manifest()
    assert manifest.pack_id == "manufacturing"
    assert "production_orders" in manifest.resources
    assert "manufacturing.release_production" in manifest.external_actions
    assert "manufacturing.production_authorization" in manifest.policies


def test_commands_declare_governed_execution():
    commands = {x["intent"]: x for x in ManufacturingPlugin(None, None).get_commands()}
    assert commands["get_production_order"]["execution_mode"] == "read_only"
    assert commands["create_production_order"]["execution_mode"] == "action_kernel"
    assert commands["release_production"]["governed_action_type"] == "manufacturing_release_production"


def test_workspace_is_authoritative_and_cross_tenant_reads_fail():
    plugin = ManufacturingPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="plant-a", role="owner"))
    try:
        wc = plugin.execute("create_work_center", {"workspace_id": "plant-b", "name": "Line 1", "capacity_units": 100}, "plant-b")["data"]["id"]
        order = plugin.execute("create_production_order", {"product_sku": "SKU-1", "reference": "MO-1", "quantity": 20, "work_center_id": wc}, "plant-b")["data"]["id"]
        assert plugin.execute("get_production_order", {"production_order_id": order}, "plant-b")["ok"] is True
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="plant-b", role="owner"))
        assert plugin.execute("get_production_order", {"production_order_id": order}, "plant-b")["ok"] is False
    finally:
        clear_request_context()


def test_production_quality_and_maintenance_flow():
    plugin = ManufacturingPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="plant-a", role="owner"))
    try:
        wc = plugin.execute("create_work_center", {"name": "Line 2", "location": "Factory A", "capacity_units": 250}, "plant-a")["data"]["id"]
        order = plugin.execute("create_production_order", {"reference": "MO-2", "product_sku": "SKU-2", "quantity": 50, "work_center_id": wc}, "plant-a")["data"]["id"]
        assert plugin.execute("update_production_status", {"production_order_id": order, "status": "in_progress"}, "plant-a")["data"]["status"] == "in_progress"
        quality = plugin.execute("record_quality", {"production_order_id": order, "result": "passed", "defect_count": 0}, "plant-a")
        assert quality["ok"] is True
        maintenance = plugin.execute("create_maintenance", {"work_center_id": wc, "description": "Inspect drive belt", "priority": "high"}, "plant-a")
        assert maintenance["ok"] is True
    finally:
        clear_request_context()


def test_invalid_production_order_rejected():
    plugin = ManufacturingPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="plant-a", role="owner"))
    try:
        result = plugin.execute("create_production_order", {"reference": "", "product_sku": "SKU", "quantity": 0}, "plant-a")
        assert result["ok"] is False
    finally:
        clear_request_context()


def test_release_and_maintenance_are_connector_deferred():
    plugin = ManufacturingPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="plant-a", role="owner"))
    try:
        release = plugin.execute("release_production", {"production_order_id": "mfg-po-1"}, "plant-a")
        request = plugin.execute("request_maintenance", {"work_center_id": "mfg-wc-1", "priority": "high"}, "plant-a")
        assert release["status"] == "action_required"
        assert release["action_type"] == "manufacturing_release_production"
        assert "connector-deferred" in release["message"]
        assert request["action_type"] == "manufacturing_request_maintenance"
    finally:
        clear_request_context()
