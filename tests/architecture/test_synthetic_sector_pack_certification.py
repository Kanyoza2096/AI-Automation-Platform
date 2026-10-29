"""Architecture certification: a new sector can be added without core changes.

This deliberately defines the synthetic pack inside the test suite rather than
adding a production sector.  The test proves the extension seam itself:
manifest metadata, workspace isolation, internal domain state, and explicit
external-action declaration.  Consequential external execution remains a
platform concern and is not implemented by the pack.
"""

from plugins.industries.base import IndustryPlugin, SectorPackManifest
from core.plugin_execution import run_plugin
from core.request_context import RequestContext, clear_request_context, set_request_context


class SyntheticFleetPlugin(IndustryPlugin):
    """Minimal certification-only sector pack."""

    pack_id = "fleet_ops_certification"
    pack_version = "0.1"
    display_name = "Fleet Operations (Certification)"
    capabilities = ("vehicles", "trips", "maintenance", "messaging")
    resources = ("vehicles", "trips", "maintenance_records")
    policies = ("fleet.workspace_access", "fleet.vehicle_access")
    workflows = ("trip_dispatch", "maintenance_due")
    events = ("vehicle.registered", "trip.dispatched", "maintenance.recorded")
    external_actions = ("send_notification",)

    def __init__(self):
        super().__init__()
        self._vehicles = []

    def get_commands(self) -> list[dict]:
        return [
            {"intent": "register_vehicle"},
            {"intent": "dispatch_trip"},
        ]

    def execute(self, command: str, params: dict, *, workspace_id: str) -> dict:
        # Internal domain state belongs to the sector pack; external side
        # effects are only declared, never executed directly here.
        if command == "register_vehicle":
            vehicle = {"workspace_id": workspace_id, "plate": params["plate"]}
            self._vehicles.append(vehicle)
            return {"status": "ok", "vehicle": vehicle}
        raise ValueError(f"Unsupported certification command: {command}")

    def get_webhooks(self) -> list[dict]:
        return [{"path": "/fleet/events"}]


def test_new_sector_pack_uses_only_the_formal_extension_contract():
    plugin = SyntheticFleetPlugin()
    manifest = plugin.get_manifest()

    assert isinstance(manifest, SectorPackManifest)
    assert manifest.pack_id == "fleet_ops_certification"
    assert manifest.version == "0.1"
    assert "vehicles" in manifest.resources
    assert "trip_dispatch" in manifest.workflows
    assert manifest.external_actions == ("send_notification",)


def test_new_sector_pack_inherits_tenant_boundary_without_core_changes():
    plugin = SyntheticFleetPlugin()
    set_request_context(
        RequestContext(
            principal_id="fleet-user",
            workspace_id="ws-fleet-a",
            role="operator",
            permissions=("read", "write"),
        )
    )
    try:
        result = run_plugin(
            plugin.execute,
            "register_vehicle",
            {"plate": "KDO-001", "workspace_id": "attacker-ws"},
            client_workspace_id="attacker-ws",
        )
    finally:
        clear_request_context()

    assert result["vehicle"]["workspace_id"] == "ws-fleet-a"
    assert plugin._vehicles == [
        {"workspace_id": "ws-fleet-a", "plate": "KDO-001"}
    ]


def test_new_sector_pack_declares_external_side_effects_but_does_not_own_execution():
    plugin = SyntheticFleetPlugin()

    assert plugin.get_external_actions() == ("send_notification",)
    # Certification rule: the synthetic pack has no provider/executor API.
    assert not hasattr(plugin, "publish")
    assert not hasattr(plugin, "send_notification")
