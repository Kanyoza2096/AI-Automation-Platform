from plugins.industries.cybersecurity import CybersecurityPlugin
from core.request_context import RequestContext, clear_request_context, set_request_context


def _plugin(): return CybersecurityPlugin(None, None)


def test_cyber_completion_manifest():
    m = _plugin().get_manifest()
    assert m.version == "1.1"
    assert "security_incidents" in m.resources
    assert "security.response.outcome.observed" in m.events
    assert "security.record_outcome" in m.external_actions


def test_security_incident_response_outcome_loop():
    p = _plugin(); set_request_context(RequestContext(principal_id="u-1", workspace_id="tenant-a", role="owner"))
    try:
        asset = p.execute("add_asset", {"name":"api","asset_type":"service","criticality":"critical"}, "tenant-a")["data"]
        finding = p.execute("create_finding", {"asset_id":asset["id"],"title":"RCE risk","severity":"critical","source":"scanner"}, "tenant-a")["data"]
        incident = p.execute("create_incident", {"title":"Active compromise","severity":"critical","source":"xdr"}, "tenant-a")["data"]
        assert p.execute("update_incident", {"incident_id":incident["id"],"status":"contained"}, "tenant-a")["ok"]
        deferred = p.execute("contain_asset", {"asset_id":asset["id"],"reason":"contain active compromise"}, "tenant-a")
        assert deferred["status"] == "action_required"
        response = p.execute("record_response_outcome", {"action_type":"security_contain_asset","target_id":asset["id"],"status":"succeeded","provider_reference":"contain-1","evidence":{"provider":"edr"}}, "tenant-a")
        assert response["status"] == "observed"
        assert p.execute("update_finding", {"finding_id":finding["id"],"status":"remediated"}, "tenant-a")["ok"]
        outcome = p.execute("record_outcome", {"resource_type":"security_incident","resource_id":incident["id"],"metric":"time_to_contain_minutes","value":14,"source":"xdr","evidence":{"window":"incident-1"}}, "tenant-a")
        assert outcome["status"] == "observed"
    finally: clear_request_context()


def test_cyber_completion_commands_are_governed():
    commands = {x["intent"]:x for x in _plugin().get_commands()}
    for name in ("create_incident","update_incident","record_response_outcome","record_outcome"):
        assert commands[name]["execution_mode"] == "action_kernel"
        assert commands[name]["governed_action_type"]
