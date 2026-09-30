from plugins.industries.it_operations.plugin import ITOperationsPlugin
from core.request_context import RequestContext, clear_request_context, set_request_context

def test_it_manifest_is_formal_and_explicit():
    m=ITOperationsPlugin(None,None).get_manifest()
    assert m.pack_id=="it_operations" and m.version=="1.1"
    assert "incidents" in m.resources and "it.remediate_service" in m.external_actions

def test_it_commands_declare_execution_semantics():
    c={x["intent"]:x for x in ITOperationsPlugin(None,None).get_commands()}
    assert c["get_incident"]["execution_mode"]=="read_only"
    assert c["request_deployment"]["execution_mode"]=="action_kernel"
    assert c["remediate_service"]["governed_action_type"]=="it_remediate_service"

def test_it_workspace_is_authenticated_context_not_client_input():
    p=ITOperationsPlugin(None,None); set_request_context(RequestContext(principal_id="u-1",workspace_id="tenant-a",role="owner"))
    try:
        r=p.execute("remediate_service",{"workspace_id":"tenant-b","service_id":"svc-1","reason":"test"},"tenant-b")
        assert r["workspace_id"]=="tenant-a"
    finally: clear_request_context()

def test_it_external_side_effects_are_connector_deferred():
    p=ITOperationsPlugin(None,None); set_request_context(RequestContext(principal_id="u-1",workspace_id="tenant-a",role="owner"))
    try:
        r=p.execute("remediate_service",{"service_id":"svc-1","reason":"cpu"},"tenant-a")
        assert r["status"]=="action_required" and r["execution_mode"]=="action_kernel"
        assert "connector layer" in r["message"]
    finally: clear_request_context()

def test_it_incident_flow_and_workspace_isolation():
    p=ITOperationsPlugin(None,None); set_request_context(RequestContext(principal_id="u-1",workspace_id="tenant-a",role="owner"))
    try:
        s=p.execute("add_service",{"name":"api","environment":"production"},"tenant-a")["data"]
        inc=p.execute("create_incident",{"service_id":s["id"],"title":"API latency","severity":"high"},"tenant-a")["data"]
        assert p.execute("get_incident",{"incident_id":inc["id"]},"tenant-a")["ok"]
        clear_request_context(); set_request_context(RequestContext(principal_id="u-2",workspace_id="tenant-b",role="owner"))
        assert p.execute("get_incident",{"incident_id":inc["id"]},"tenant-b")["ok"] is False
    finally: clear_request_context()
