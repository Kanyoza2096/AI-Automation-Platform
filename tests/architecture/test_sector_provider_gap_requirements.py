from services.sector_provider_gap_requirements import build_sector_provider_gap_requirements


def test_all_unbound_sector_actions_have_explicit_non_authoritative_requirements():
    result = build_sector_provider_gap_requirements()
    assert result["schema"] == "KDO.SectorProviderGapRequirements/v1"
    assert result["read_only"] is True
    assert result["authority"]["this_surface_grants_execution"] is False
    assert result["authority"]["this_surface_selects_a_provider"] is False
    assert result["summary"]["unbound_actions"] == 8

    actions = {item["sector_action"]: item for item in result["gaps"]}
    expected = {
        "security.contain_asset",
        "security.remediate_finding",
        "supply_chain.dispatch_shipment",
        "supply_chain.request_carrier_booking",
        "manufacturing.release_production",
        "energy.issue_remote_command",
        "agriculture.dispatch_field_task",
        "agriculture.request_input_delivery",
    }
    assert set(actions) == expected
    for item in actions.values():
        assert item["status"] == "NOT_BOUND"
        assert item["required_capability"]
        assert item["required_provider_semantics"]
        assert item["required_receipt"]
        assert item["verification"]
        assert item["reconciliation"]
        assert item["minimum_binding_gates"]
