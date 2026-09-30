from core.request_context import RequestContext, clear_request_context, set_request_context
from plugins.industries.government.plugin import GovernmentPlugin

def test_manifest_v11_declares_resolution_and_outcome_loop():
    m = GovernmentPlugin(None, None).get_manifest()
    assert m.version == "1.1"
    assert "service_resolutions" in m.resources
    assert "public_service_outcomes" in m.resources
    assert "government.record_outcome" in m.external_actions
    assert "government.outcome_integrity" in m.policies

def test_resolution_and_outcome_loop_is_tenant_scoped():
    plugin = GovernmentPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="gov-a", role="owner"))
    try:
        service = plugin.execute("create_service", {"name":"Road Maintenance","category":"public_works"}, "gov-a")["data"]["id"]
        req = plugin.execute("create_service_request", {"service_id":service,"title":"Pothole repair"}, "gov-a")["data"]["id"]
        resolution = plugin.execute("record_resolution", {"request_id":req,"resolution":"Crew dispatched and repair completed","evidence_ref":"work-order-42"}, "gov-a")
        assert resolution["ok"]
        outcome = plugin.execute("record_outcome", {"reference_type":"service_request","reference_id":req,"outcome_type":"resolved","metric":"resolution_hours","value":18.5,"evidence_ref":"work-order-42"}, "gov-a")
        assert outcome["ok"]
        clear_request_context(); set_request_context(RequestContext(principal_id="u-2", workspace_id="gov-b", role="owner"))
        assert plugin.execute("get_resolution", {"resolution_id":resolution["data"]["id"]}, "gov-b")["ok"] is False
        assert plugin.execute("get_outcome", {"outcome_id":outcome["data"]["id"]}, "gov-b")["ok"] is False
    finally:
        clear_request_context()
