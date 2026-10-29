from plugins.industries.commerce.plugin import CommercePlugin
from core.request_context import RequestContext, clear_request_context, set_request_context


def _plugin():
    return CommercePlugin(None, None)


def test_commerce_completion_manifest():
    m = _plugin().get_manifest()
    assert m.version == "1.1"
    assert "inventory_reservations" in m.resources
    assert "commerce.outcome.observed" in m.events
    assert "commerce_record_payment" in m.external_actions or "commerce.capture_payment" in m.external_actions


def test_order_inventory_payment_fulfillment_outcome_loop():
    p = _plugin()
    set_request_context(RequestContext(principal_id="u-1", workspace_id="tenant-a", role="owner"))
    try:
        customer = p.execute("add_customer", {"name": "Buyer"}, "tenant-a")["data"]
        product = p.execute("add_product", {"name": "Widget", "sku": "W-1", "price": 25, "inventory_quantity": 10}, "tenant-a")["data"]
        order = p.execute("create_order", {"customer_id": customer["id"], "items": [{"product_id": product["id"], "quantity": 2}]}, "tenant-a")["data"]
        reserved = p.execute("reserve_inventory", {"order_id": order["id"]}, "tenant-a")
        assert reserved["status"] == "reserved"
        payment = p.execute("record_payment", {"order_id": order["id"], "amount": 50, "currency": "MWK", "status": "captured", "provider_reference": "pay-123"}, "tenant-a")
        assert payment["data"]["status"] == "captured"
        fulfillment = p.execute("record_fulfillment", {"order_id": order["id"], "status": "shipped", "carrier": "carrier-x", "tracking_reference": "trk-1"}, "tenant-a")
        assert fulfillment["data"]["status"] == "shipped"
        outcome = p.execute("record_outcome", {"order_id": order["id"], "metric": "gross_value", "value": 50, "source": "orders", "evidence": {"window": "order"}}, "tenant-a")
        assert outcome["status"] == "observed"
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="tenant-b", role="owner"))
        assert p.execute("get_order", {"order_id": order["id"]}, "tenant-b")["ok"] is False
    finally:
        clear_request_context()


def test_external_commerce_actions_remain_connector_deferred():
    p = _plugin()
    set_request_context(RequestContext(principal_id="u-1", workspace_id="tenant-a", role="owner"))
    try:
        for command in ("capture_payment", "fulfill_order"):
            result = p.execute(command, {"order_id": "ord-1"}, "tenant-a")
            assert result["status"] == "action_required"
            assert result["execution_mode"] == "action_kernel"
            assert "connector layer" in result["message"]
    finally:
        clear_request_context()


def test_completion_commands_are_governed():
    commands = {x["intent"]: x for x in _plugin().get_commands()}
    for name in ("reserve_inventory", "record_payment", "record_fulfillment", "record_outcome"):
        assert commands[name]["execution_mode"] == "action_kernel"
        assert commands[name]["governed_action_type"]
