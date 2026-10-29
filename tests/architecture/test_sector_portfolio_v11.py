"""Portfolio-level certification for the complete 13-sector v1.1 release."""

import importlib


TARGET_PACKS = {
    "marketing_media": "MarketingMediaPlugin",
    "commerce": "CommercePlugin",
    "it_operations": "ITOperationsPlugin",
    "cybersecurity": "CybersecurityPlugin",
    "crm": "CRMPlugin",
    "education": "EducationPlugin",
    "healthcare": "HealthcarePlugin",
    "finance": "FinancePlugin",
    "supply_chain": "SupplyChainPlugin",
    "manufacturing": "ManufacturingPlugin",
    "energy": "EnergyPlugin",
    "government": "GovernmentPlugin",
    "agriculture": "AgriculturePlugin",
}


def _plugins():
    for module_name, class_name in TARGET_PACKS.items():
        module = importlib.import_module(f"plugins.industries.{module_name}.plugin")
        yield module_name, getattr(module, class_name)(None, None)


def test_complete_sector_portfolio_is_v11_and_unique():
    plugins = list(_plugins())
    assert len(plugins) == 13
    pack_ids = []
    for module_name, plugin in plugins:
        manifest = plugin.get_manifest()
        assert manifest.version == "1.1", module_name
        assert manifest.pack_id == module_name, module_name
        assert manifest.capabilities
        assert manifest.resources
        assert manifest.policies
        assert manifest.workflows
        assert manifest.events
        assert isinstance(manifest.external_actions, tuple)
        pack_ids.append(manifest.pack_id)
    assert len(set(pack_ids)) == 13


def test_external_action_namespace_is_collision_free():
    actions = []
    for _module_name, plugin in _plugins():
        actions.extend(plugin.get_manifest().external_actions)
    assert actions
    # Shared canonical actions are permitted; duplicate sector actions are not.
    shared = {"publish_content"}
    sector_actions = [a for a in actions if a not in shared]
    assert len(sector_actions) == len(set(sector_actions))


def test_sector_manifests_remain_declared_metadata_and_legacy_contracts():
    for module_name, plugin in _plugins():
        manifest = plugin.get_manifest()
        assert plugin.get_capabilities() == manifest.capabilities
        assert plugin.get_resources() == manifest.resources
        assert callable(plugin.get_commands)
        assert callable(plugin.execute)
        assert callable(plugin.get_webhooks)
        assert callable(plugin.get_external_actions)
