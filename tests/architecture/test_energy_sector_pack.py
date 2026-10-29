from core.request_context import RequestContext, clear_request_context, set_request_context
from plugins.industries.energy.plugin import EnergyPlugin


def test_energy_manifest_is_formal():
    manifest = EnergyPlugin(None, None).get_manifest()
    assert manifest.pack_id == "energy"
    assert "meter_readings" in manifest.resources
    assert "energy.issue_remote_command" in manifest.external_actions
    assert "energy.remote_command_authorization" in manifest.policies


def test_commands_declare_governed_execution():
    commands = {x["intent"]: x for x in EnergyPlugin(None, None).get_commands()}
    assert commands["get_asset"]["execution_mode"] == "read_only"
    assert commands["create_incident"]["execution_mode"] == "action_kernel"
    assert commands["issue_remote_command"]["governed_action_type"] == "energy_issue_remote_command"


def test_workspace_is_authoritative_and_cross_tenant_reads_fail():
    plugin = EnergyPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="utility-a", role="owner"))
    try:
        asset = plugin.execute("create_asset", {"workspace_id": "utility-b", "name": "Substation 1", "asset_type": "substation"}, "utility-b")["data"]["id"]
        assert plugin.execute("get_asset", {"asset_id": asset}, "utility-b")["ok"] is True
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="utility-b", role="owner"))
        assert plugin.execute("get_asset", {"asset_id": asset}, "utility-b")["ok"] is False
    finally:
        clear_request_context()


def test_meter_incident_and_work_order_flow():
    plugin = EnergyPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="utility-a", role="owner"))
    try:
        asset = plugin.execute("create_asset", {"name": "Feeder 1", "asset_type": "feeder", "location": "North"}, "utility-a")["data"]["id"]
        meter = plugin.execute("record_meter", {"asset_id": asset, "metric": "load", "value": 42.5, "unit": "kW"}, "utility-a")
        assert meter["ok"] is True
        incident = plugin.execute("create_incident", {"asset_id": asset, "title": "Voltage fluctuation", "severity": "high"}, "utility-a")
        incident_id = incident["data"]["id"]
        assert plugin.execute("update_incident", {"incident_id": incident_id, "status": "acknowledged"}, "utility-a")["data"]["status"] == "acknowledged"
        work = plugin.execute("create_work_order", {"asset_id": asset, "description": "Inspect feeder", "priority": "high"}, "utility-a")
        assert work["ok"] is True
    finally:
        clear_request_context()


def test_invalid_asset_rejected():
    plugin = EnergyPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="utility-a", role="owner"))
    try:
        result = plugin.execute("record_meter", {"asset_id": "missing", "metric": "load", "value": 10, "unit": "kW"}, "utility-a")
        assert result["ok"] is False
    finally:
        clear_request_context()


def test_external_utility_actions_are_connector_deferred():
    plugin = EnergyPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="utility-a", role="owner"))
    try:
        dispatch = plugin.execute("dispatch_crew", {"incident_id": "inc-1", "crew_id": "crew-1"}, "utility-a")
        remote = plugin.execute("issue_remote_command", {"asset_id": "asset-1", "command": "open_breaker"}, "utility-a")
        assert dispatch["status"] == "action_required"
        assert dispatch["action_type"] == "energy_dispatch_crew"
        assert "connector-deferred" in dispatch["message"]
        assert remote["action_type"] == "energy_issue_remote_command"
    finally:
        clear_request_context()
