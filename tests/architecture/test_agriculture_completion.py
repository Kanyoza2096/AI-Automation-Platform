from core.request_context import RequestContext, clear_request_context, set_request_context
from plugins.industries.agriculture.plugin import AgriculturePlugin


def test_agriculture_completion_manifest():
    m = AgriculturePlugin(None, None).get_manifest()
    assert m.version == "1.1"
    assert "field_exceptions" in m.resources
    assert "agricultural_outcomes" in m.resources
    assert "agriculture.record_outcome" in m.external_actions
    assert "agriculture.outcome_integrity" in m.policies


def test_exception_and_outcome_loop():
    p = AgriculturePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="farm-a", role="owner"))
    try:
        farm = p.execute("create_farm", {"name": "North Farm", "farm_type": "crop"}, "farm-a")["data"]["id"]
        field = p.execute("create_field", {"farm_id": farm, "name": "Field 1", "area": 12.5}, "farm-a")["data"]["id"]
        cycle = p.execute("create_crop_cycle", {"field_id": field, "crop": "maize", "season": "2026-main"}, "farm-a")["data"]["id"]
        exc = p.execute("record_exception", {"field_id": field, "exception_type": "pest_pressure", "severity": "high", "evidence_ref": "obs-1"}, "farm-a")
        assert exc["ok"] is True
        assert exc["data"]["status"] == "open"
        out = p.execute("record_outcome", {"reference_type": "crop_cycle", "reference_id": cycle, "outcome_type": "yield", "metric": "yield", "value": 4.2, "unit": "t_per_ha", "evidence_ref": "harvest-1"}, "farm-a")
        assert out["ok"] is True
        assert out["data"]["evidence_ref"] == "harvest-1"
        assert out["data"]["unit"] == "t_per_ha"
    finally:
        clear_request_context()


def test_completion_is_tenant_isolated():
    p = AgriculturePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="farm-a", role="owner"))
    try:
        farm = p.execute("create_farm", {"name": "North Farm", "farm_type": "crop"}, "farm-a")["data"]["id"]
        field = p.execute("create_field", {"farm_id": farm, "name": "Field 1", "area": 10}, "farm-a")["data"]["id"]
        exc = p.execute("record_exception", {"field_id": field, "exception_type": "flooding"}, "farm-a")["data"]["id"]
        out = p.execute("record_outcome", {"reference_type": "field", "reference_id": field, "outcome_type": "availability", "value": 0.8}, "farm-a")["data"]["id"]
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="farm-b", role="owner"))
        assert p.execute("get_exception", {"exception_id": exc}, "farm-b")["ok"] is False
        assert p.execute("get_outcome", {"outcome_id": out}, "farm-b")["ok"] is False
    finally:
        clear_request_context()
