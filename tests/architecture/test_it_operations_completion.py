from plugins.industries.it_operations.plugin import ITOperationsPlugin
from core.request_context import RequestContext, clear_request_context, set_request_context

def _plugin(): return ITOperationsPlugin(None, None)

def test_it_completion_manifest():
    m=_plugin().get_manifest()
    assert m.version=="1.1"
    assert "incident_updates" in m.resources
    assert "deployment.outcome.observed" in m.events
    assert "it.remediate_service" in m.external_actions

def test_incident_deployment_service_outcome_loop():
    p=_plugin(); set_request_context(RequestContext(principal_id="u-1",workspace_id="tenant-a",role="owner"))
    try:
        service=p.execute("add_service",{"name":"api","environment":"production"},"tenant-a")["data"]
        incident=p.execute("create_incident",{"service_id":service["id"],"title":"Latency","severity":"high"},"tenant-a")["data"]
        update=p.execute("add_incident_update",{"incident_id":incident["id"],"status":"acknowledged","note":"on-call engaged"},"tenant-a")
        assert update["ok"] is True
        deployment=p.execute("request_deployment",{"service_id":service["id"],"version":"2026.09.30.1"},"tenant-a")["data"]
        outcome=p.execute("record_deployment_outcome",{"deployment_id":deployment["id"],"status":"succeeded","provider_reference":"deploy-1","evidence":{"commit":"abc"}},"tenant-a")
        assert outcome["status"]=="observed"
        service_outcome=p.execute("record_service_outcome",{"service_id":service["id"],"metric":"availability","value":99.95,"source":"monitoring","evidence":{"window":"24h"}},"tenant-a")
        assert service_outcome["status"]=="observed"
    finally: clear_request_context()

def test_it_completion_commands_are_governed():
    commands={x["intent"]:x for x in _plugin().get_commands()}
    for name in ("add_incident_update","record_deployment_outcome","record_service_outcome"):
        assert commands[name]["execution_mode"]=="action_kernel"
        assert commands[name]["governed_action_type"]
