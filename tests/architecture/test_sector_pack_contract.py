"""Certification tests for the formal sector-pack contract."""

from plugins.industries.church_mis.plugin import ChurchMISPlugin
from plugins.industries.commerce.plugin import CommercePlugin
from plugins.industries.crm.plugin import CRMPlugin
from plugins.industries.hospital.plugin import HospitalMISPlugin
from plugins.industries.healthcare.plugin import HealthcarePlugin
from plugins.industries.pdf_merger.plugin import PDFMergerPlugin
from plugins.industries.school_mis.plugin import SchoolMISPlugin


SECTOR_PLUGINS = [
    CRMPlugin,
    SchoolMISPlugin,
    HospitalMISPlugin,
    HealthcarePlugin,
    ChurchMISPlugin,
    CommercePlugin,
    PDFMergerPlugin,
]


def test_all_sector_plugins_expose_formal_manifest():
    manifests = [plugin(None, None).get_manifest() for plugin in SECTOR_PLUGINS]
    assert len({m.pack_id for m in manifests}) == len(manifests)
    for manifest in manifests:
        assert manifest.pack_id
        assert manifest.version
        assert manifest.display_name
        assert manifest.capabilities
        assert manifest.resources


def test_manifest_is_metadata_only_and_does_not_replace_legacy_contract():
    for plugin_cls in SECTOR_PLUGINS:
        plugin = plugin_cls(None, None)
        assert callable(plugin.get_commands)
        assert callable(plugin.execute)
        assert callable(plugin.get_webhooks)
        assert plugin.get_capabilities() == plugin.get_manifest().capabilities
        assert plugin.get_resources() == plugin.get_manifest().resources


def test_external_actions_are_explicitly_declared():
    for plugin_cls in SECTOR_PLUGINS:
        plugin = plugin_cls(None, None)
        actions = plugin.get_external_actions()
        assert isinstance(actions, tuple)
