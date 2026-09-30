from core.request_context import RequestContext, clear_request_context, set_request_context
from plugins.industries.supply_chain.plugin import SupplyChainPlugin


def test_supply_chain_completion_manifest():
    m = SupplyChainPlugin(None, None).get_manifest()
    assert m.version == "1.1"
    assert "shipment_exceptions" in m.resources
    assert "logistics_outcomes" in m.resources
    assert "supply_chain.record_outcome" in m.external_actions


def test_exception_and_outcome_loop():
    p = SupplyChainPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="logistics-a", role="owner"))
    try:
        shipment = p.execute("create_shipment", {"reference":"S-C", "origin":"A", "destination":"B"}, "logistics-a")["data"]["id"]
        exc = p.execute("record_exception", {"shipment_id":shipment, "exception_type":"delay", "severity":"high"}, "logistics-a")
        assert exc["ok"] is True
        assert exc["data"]["status"] == "open"
        out = p.execute("record_outcome", {"reference_type":"shipment", "reference_id":shipment, "outcome_type":"on_time_rate", "metric":"delivery_rate", "value":0.92, "evidence_ref":"evt-1"}, "logistics-a")
        assert out["ok"] is True
        assert out["data"]["evidence_ref"] == "evt-1"
    finally:
        clear_request_context()


def test_completion_is_tenant_isolated():
    p = SupplyChainPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="logistics-a", role="owner"))
    try:
        shipment = p.execute("create_shipment", {"reference":"S-I", "origin":"A", "destination":"B"}, "logistics-a")["data"]["id"]
        exc = p.execute("record_exception", {"shipment_id":shipment, "exception_type":"damage"}, "logistics-a")["data"]["id"]
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="logistics-b", role="owner"))
        assert p.execute("get_exception", {"exception_id":exc}, "logistics-b")["ok"] is False
    finally:
        clear_request_context()
