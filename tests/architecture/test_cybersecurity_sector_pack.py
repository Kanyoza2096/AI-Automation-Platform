from plugins.industries.cybersecurity import CybersecurityPlugin
from core.request_context import RequestContext, clear_request_context, set_request_context


def test_cyber_manifest_is_formal_and_explicit():
    m=CybersecurityPlugin(None,None).get_manifest()
    assert m.pack_id=="cybersecurity" and m.version=="1.1"
    assert "findings" in m.resources and "security.contain_asset" in m.external_actions


def test_cyber_commands_declare_execution_semantics():
    c={x["intent"]:x for x in CybersecurityPlugin(None,None).get_commands()}
    assert c["get_finding"]["execution_mode"]=="read_only"
    assert c["create_finding"]["execution_mode"]=="action_kernel"
    assert c["contain_asset"]["governed_action_type"]=="security_contain_asset"


def test_cyber_workspace_is_authenticated_context_not_client_input():
    p=CybersecurityPlugin(None,None)
    set_request_context(RequestContext(principal_id="u-1",workspace_id="tenant-a",role="owner"))
    try:
        r=p.execute("contain_asset",{"workspace_id":"tenant-b","asset_id":"ast-1","reason":"test"},"tenant-b")
        assert r["workspace_id"]=="tenant-a"
    finally: clear_request_context()


def test_cyber_external_side_effects_are_connector_deferred():
    p=CybersecurityPlugin(None,None)
    set_request_context(RequestContext(principal_id="u-1",workspace_id="tenant-a",role="owner"))
    try:
        r=p.execute("contain_asset",{"asset_id":"ast-1","reason":"suspected compromise"},"tenant-a")
        assert r["status"]=="action_required" and r["execution_mode"]=="action_kernel"
        assert "connector layer" in r["message"]
    finally: clear_request_context()


def test_cyber_finding_flow_and_workspace_isolation():
    p=CybersecurityPlugin(None,None)
    set_request_context(RequestContext(principal_id="u-1",workspace_id="tenant-a",role="owner"))
    try:
        a=p.execute("add_asset",{"name":"api","asset_type":"service","criticality":"high"},"tenant-a")["data"]
        f=p.execute("create_finding",{"asset_id":a["id"],"title":"Weak TLS","severity":"high","source":"scanner"},"tenant-a")["data"]
        assert p.execute("get_finding",{"finding_id":f["id"]},"tenant-a")["ok"]
        clear_request_context(); set_request_context(RequestContext(principal_id="u-2",workspace_id="tenant-b",role="owner"))
        assert p.execute("get_finding",{"finding_id":f["id"]},"tenant-b")["ok"] is False
    finally: clear_request_context()


def test_cyber_alert_flow_and_validation():
    p=CybersecurityPlugin(None,None)
    set_request_context(RequestContext(principal_id="u-1",workspace_id="tenant-a",role="owner"))
    try:
        a=p.execute("add_asset",{"name":"edge-fw","asset_type":"network","criticality":"critical"},"tenant-a")["data"]
        alert=p.execute("create_alert",{"asset_id":a["id"],"title":"Suspicious egress","severity":"critical","source":"xdr"})["data"]
        assert alert["asset_id"]==a["id"] and alert["severity"]=="critical"
    finally: clear_request_context()
