from services.sector_action_semantic_binding_audit import build_sector_action_semantic_binding_audit

def test_semantic_binding_audit_is_read_only_and_covers_all_blocked_actions():
    result=build_sector_action_semantic_binding_audit()
    assert result["read_only"] is True
    assert result["summary"]["blocked_actions"] == 8
    assert sum(result["summary"]["classifications"].values()) == 8

def test_audit_does_not_promote_unbound_actions():
    result=build_sector_action_semantic_binding_audit()
    assert all(x["classification"] != "BOUND" for x in result["actions"])
    assert result["actions"]

def test_high_risk_missing_provider_semantics_are_explicit():
    by={x["sector_action"]:x for x in build_sector_action_semantic_binding_audit()["actions"]}
    assert by["manufacturing.release_production"]["classification"] == "NO_SEMANTIC_PROVIDER_BINDING"
    assert by["energy.issue_remote_command"]["classification"] == "NO_SEMANTIC_PROVIDER_BINDING"
    assert by["supply_chain.request_carrier_booking"]["classification"] == "NO_SEMANTIC_PROVIDER_BINDING"
    assert by["agriculture.dispatch_field_task"]["classification"] == "NO_SEMANTIC_PROVIDER_BINDING"
