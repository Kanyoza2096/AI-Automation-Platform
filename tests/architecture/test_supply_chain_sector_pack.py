from core.request_context import RequestContext, clear_request_context, set_request_context
from plugins.industries.supply_chain.plugin import SupplyChainPlugin


def test_supply_chain_manifest_is_formal():
    manifest = SupplyChainPlugin(None, None).get_manifest()
    assert manifest.pack_id == "supply_chain"
    assert "shipments" in manifest.resources
    assert "supply_chain.dispatch_shipment" in manifest.external_actions
    assert "supply_chain.carrier_authorization" in manifest.policies


def test_commands_declare_execution_semantics():
    commands = {item["intent"]: item for item in SupplyChainPlugin(None, None).get_commands()}
    assert commands["get_shipment"]["execution_mode"] == "read_only"
    assert commands["create_shipment"]["execution_mode"] == "action_kernel"
    assert commands["dispatch_shipment"]["governed_action_type"] == "supply_chain_dispatch_shipment"


def test_workspace_is_authoritative():
    plugin = SupplyChainPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="logistics-a", role="owner"))
    try:
        result = plugin.execute("create_shipment", {"workspace_id": "logistics-b", "reference": "S-1", "origin": "A", "destination": "B"}, "logistics-b")
        assert result["workspace_id"] == "logistics-a"
        assert result["ok"] is True
    finally:
        clear_request_context()


def test_shipment_inventory_and_purchase_order_flow_is_tenant_isolated():
    plugin = SupplyChainPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="logistics-a", role="owner"))
    try:
        shipment = plugin.execute("create_shipment", {"reference": "S-1", "origin": "A", "destination": "B"}, "logistics-a")["data"]["id"]
        inventory = plugin.execute("upsert_inventory", {"sku": "SKU-1", "warehouse": "WH-A", "quantity": 4, "reorder_point": 5}, "logistics-a")["data"]["id"]
        po = plugin.execute("create_purchase_order", {"supplier_ref": "SUP-1", "reference": "PO-1", "total_amount": 10000}, "logistics-a")["data"]["id"]
        assert plugin.execute("get_shipment", {"shipment_id": shipment}, "logistics-a")["ok"] is True
        assert plugin.execute("get_inventory", {"inventory_id": inventory}, "logistics-a")["data"]["status"] == "reorder_required"
        assert plugin.execute("get_purchase_order", {"purchase_order_id": po}, "logistics-a")["ok"] is True
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="logistics-b", role="owner"))
        assert plugin.execute("get_shipment", {"shipment_id": shipment}, "logistics-b")["ok"] is False
        assert plugin.execute("get_inventory", {"inventory_id": inventory}, "logistics-b")["ok"] is False
        assert plugin.execute("get_purchase_order", {"purchase_order_id": po}, "logistics-b")["ok"] is False
    finally:
        clear_request_context()


def test_shipment_status_lifecycle():
    plugin = SupplyChainPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="logistics-a", role="owner"))
    try:
        shipment = plugin.execute("create_shipment", {"reference": "S-2", "origin": "A", "destination": "B"}, "logistics-a")["data"]["id"]
        result = plugin.execute("update_shipment_status", {"shipment_id": shipment, "status": "in_transit"}, "logistics-a")
        assert result["ok"] is True
        assert result["data"]["status"] == "in_transit"
    finally:
        clear_request_context()


def test_invalid_inventory_rejected():
    plugin = SupplyChainPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="logistics-a", role="owner"))
    try:
        result = plugin.execute("upsert_inventory", {"sku": "", "warehouse": "WH", "quantity": 1}, "logistics-a")
        assert result["ok"] is False
    finally:
        clear_request_context()


def test_external_dispatch_is_connector_deferred():
    plugin = SupplyChainPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="logistics-a", role="owner"))
    try:
        result = plugin.execute("dispatch_shipment", {"shipment_id": "sc-shp-1", "carrier": "carrier-x"}, "logistics-a")
        assert result["status"] == "action_required"
        assert result["execution_mode"] == "action_kernel"
        assert result["action_type"] == "supply_chain_dispatch_shipment"
        assert "connector-deferred" in result["message"]
    finally:
        clear_request_context()
