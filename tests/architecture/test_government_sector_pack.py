from core.request_context import RequestContext, clear_request_context, set_request_context
from plugins.industries.government.plugin import GovernmentPlugin


def test_government_manifest_is_formal():
    manifest = GovernmentPlugin(None, None).get_manifest()
    assert manifest.pack_id == "government"
    assert "service_requests" in manifest.resources
    assert "government.notify_applicant" in manifest.external_actions
    assert "government.case_authorization" in manifest.policies


def test_commands_declare_governed_execution():
    commands = {x["intent"]: x for x in GovernmentPlugin(None, None).get_commands()}
    assert commands["get_service"]["execution_mode"] == "read_only"
    assert commands["create_service_request"]["execution_mode"] == "action_kernel"
    assert commands["notify_applicant"]["governed_action_type"] == "government_notify_applicant"


def test_workspace_is_authoritative_and_cross_tenant_reads_fail():
    plugin = GovernmentPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="gov-a", role="owner"))
    try:
        service = plugin.execute("create_service", {"workspace_id": "gov-b", "name": "Waste Collection", "category": "municipal"}, "gov-b")["data"]["id"]
        assert plugin.execute("get_service", {"service_id": service}, "gov-b")["ok"] is True
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="gov-b", role="owner"))
        assert plugin.execute("get_service", {"service_id": service}, "gov-b")["ok"] is False
    finally:
        clear_request_context()


def test_request_permit_and_case_flow():
    plugin = GovernmentPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="gov-a", role="owner"))
    try:
        service = plugin.execute("create_service", {"name": "Building Permits", "category": "permits"}, "gov-a")["data"]["id"]
        request = plugin.execute("create_service_request", {"service_id": service, "title": "Permit application support", "priority": "high"}, "gov-a")
        request_id = request["data"]["id"]
        assert plugin.execute("update_service_request", {"request_id": request_id, "status": "in_progress"}, "gov-a")["data"]["status"] == "in_progress"
        permit = plugin.execute("create_permit_case", {"service_id": service, "applicant_ref": "APP-001", "permit_type": "construction"}, "gov-a")
        assert permit["ok"] is True
        case = plugin.execute("create_case", {"service_id": service, "title": "Permit review case"}, "gov-a")
        assert case["ok"] is True
    finally:
        clear_request_context()


def test_invalid_service_rejected():
    plugin = GovernmentPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="gov-a", role="owner"))
    try:
        result = plugin.execute("create_service_request", {"service_id": "missing", "title": "Request"}, "gov-a")
        assert result["ok"] is False
    finally:
        clear_request_context()


def test_external_public_service_actions_are_connector_deferred():
    plugin = GovernmentPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="gov-a", role="owner"))
    try:
        notify = plugin.execute("notify_applicant", {"case_id": "case-1", "channel": "sms"}, "gov-a")
        dispatch = plugin.execute("dispatch_field_service", {"request_id": "req-1", "team_id": "team-1"}, "gov-a")
        assert notify["status"] == "action_required"
        assert notify["action_type"] == "government_notify_applicant"
        assert "connector-deferred" in notify["message"]
        assert dispatch["action_type"] == "government_dispatch_field_service"
    finally:
        clear_request_context()
