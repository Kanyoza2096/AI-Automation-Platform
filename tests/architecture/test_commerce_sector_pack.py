"""Production-boundary certification for the Commerce sector pack."""

from plugins.industries.commerce.plugin import CommercePlugin
from core.request_context import RequestContext, clear_request_context, set_request_context


def test_commerce_manifest_is_formal_and_explicit():
    manifest = CommercePlugin(None, None).get_manifest()
    assert manifest.pack_id == "commerce"
    assert manifest.version == "1.1"
    assert "orders" in manifest.resources
    assert "commerce.capture_payment" in manifest.external_actions
    assert "commerce.record_payment" in manifest.external_actions
    assert "order_lifecycle" in manifest.workflows


def test_commerce_commands_declare_execution_semantics():
    commands = {item["intent"]: item for item in CommercePlugin(None, None).get_commands()}
    assert commands["get_order"]["execution_mode"] == "read_only"
    assert commands["create_order"]["execution_mode"] == "action_kernel"
    assert commands["capture_payment"]["governed_action_type"] == "commerce_capture_payment"


def test_commerce_workspace_is_authenticated_context_not_client_input():
    plugin = CommercePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="tenant-a", role="owner"))
    try:
        result = plugin.execute("capture_payment", {"workspace_id": "tenant-b", "order_id": "ord-1", "amount": 100}, "tenant-b")
        assert result["workspace_id"] == "tenant-a"
    finally:
        clear_request_context()


def test_commerce_external_side_effects_are_connector_deferred():
    plugin = CommercePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="tenant-a", role="owner"))
    try:
        result = plugin.execute("capture_payment", {"order_id": "ord-1", "amount": 100}, "tenant-a")
        assert result["status"] == "action_required"
        assert result["execution_mode"] == "action_kernel"
        assert "connector layer" in result["message"]
    finally:
        clear_request_context()


def test_commerce_order_flow_and_workspace_isolation():
    plugin = CommercePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="tenant-a", role="owner"))
    try:
        customer = plugin.execute("add_customer", {"name": "A Customer"}, "tenant-a")["data"]
        product = plugin.execute("add_product", {"name": "Widget", "sku": "W-1", "price": 25, "inventory_quantity": 10}, "tenant-a")["data"]
        order = plugin.execute("create_order", {"customer_id": customer["id"], "items": [{"product_id": product["id"], "quantity": 2}]}, "tenant-a")["data"]
        assert order["total_amount"] == 50
        assert plugin.execute("get_order", {"order_id": order["id"]}, "tenant-a")["ok"] is True
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="tenant-b", role="owner"))
        assert plugin.execute("get_order", {"order_id": order["id"]}, "tenant-b")["ok"] is False
    finally:
        clear_request_context()
