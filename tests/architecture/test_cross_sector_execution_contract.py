"""Cross-sector execution contract certification.

A synthetic sector action must use the platform's canonical execution plane:
intent -> governance contract -> durable ledger -> external receipt -> replay/
reconciliation semantics.  The sector itself must not create a second executor.
"""

import asyncio

from plugins.industries.base import IndustryPlugin
from services.action_kernel import ActionContract, ActionKernel


class SyntheticFleetPlugin(IndustryPlugin):
    pack_id = "fleet_ops_execution_certification"
    pack_version = "1.0"
    display_name = "Fleet Operations Certification Pack"
    capabilities = ("vehicles", "dispatch", "notifications")
    resources = ("vehicles", "trips")
    policies = ("fleet.workspace_access", "fleet.vehicle_access")
    workflows = ("dispatch_trip",)
    events = ("trip.created", "trip.dispatched")
    external_actions = ("fleet.notify_dispatch",)

    def get_commands(self):
        return [{"intent": "dispatch_trip"}]

    def execute(self, command, params):
        return {"ok": True, "command": command}

    def get_webhooks(self):
        return []


def test_new_sector_action_uses_canonical_execution_plane_and_replays_safely():
    plugin = SyntheticFleetPlugin(None, None)
    manifest = plugin.get_manifest()
    assert "fleet.notify_dispatch" in manifest.external_actions

    calls = []

    def provider_operation(params, _intent):
        calls.append(params["vehicle_id"])
        return {"ok": True, "object_id": "dispatch-001", "request_id": "req-001"}

    kernel = ActionKernel(None, allow_in_memory=True)
    kernel.register(
        ActionContract(
            "fleet_notify_dispatch",
            risk_level="low",
            handler=provider_operation,
        )
    )

    first = asyncio.run(
        kernel.execute(
            workspace_id="tenant-fleet",
            action_type="fleet_notify_dispatch",
            parameters={"vehicle_id": "VH-7", "message": "Trip dispatched"},
            risk_level="low",
            idempotency_key="fleet-dispatch-7",
        )
    )
    second = asyncio.run(
        kernel.execute(
            workspace_id="tenant-fleet",
            action_type="fleet_notify_dispatch",
            parameters={"vehicle_id": "VH-7", "message": "Trip dispatched"},
            idempotency_key="fleet-dispatch-7",
            risk_level="low",
        )
    )

    assert first["status"] == "succeeded"
    assert first["output"]["object_id"] == "dispatch-001"
    assert first["execution_id"]
    assert second["status"] == "succeeded"
    assert second["replayed"] is True
    assert second["execution_id"] == first["execution_id"]
    assert calls == ["VH-7"]


def test_external_success_without_receipt_is_uncertain_not_failed():
    calls = []

    def ambiguous_provider(params, _intent):
        calls.append(params["vehicle_id"])
        return {"ok": True}

    kernel = ActionKernel(None, allow_in_memory=True)
    kernel.register(
        ActionContract(
            "fleet_notify_ambiguous",
            risk_level="low",
            handler=ambiguous_provider,
        )
    )

    result = asyncio.run(
        kernel.execute(
            workspace_id="tenant-fleet",
            action_type="fleet_notify_ambiguous",
            parameters={"vehicle_id": "VH-9"},
            risk_level="low",
            idempotency_key="fleet-ambiguous-9",
        )
    )

    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True
    assert "object ID" in result["output"]["error"]
    assert calls == ["VH-9"]
