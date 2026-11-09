"""Verified provenance transport tests for the in-process UnifiedEventBus.

These tests intentionally do not confer trust on SharedEventBus, database
replay, payload metadata, or legacy source labels.
"""
import asyncio

from core.event_provenance import ActorPrincipal, ProducerRegistry
from core.unified_bus import UnifiedEventBus


def test_verified_capability_is_carried_out_of_band_to_live_handlers():
    async def run():
        registry = ProducerRegistry({"health-monitor": "internal_health_producer"})
        capability = registry.issue("health-monitor")
        actor = ActorPrincipal(
            principal_type="human",
            subject_id="user-1",
            auth_method="supabase_jwt",
            workspace_id="workspace-a",
        )
        # Exercise the validated actor context contract without fabricating
        # an API request; production obtains this ContextVar from require_auth.
        from api.support import auth_identity_var

        class Identity:
            valid = True
            method = "supabase_jwt"
            user_id = "user-1"
            workspace_id = "workspace-a"
            is_species = False

        token = auth_identity_var.set(Identity())
        try:
            bus = UnifiedEventBus(producer_registry=registry)
            received = asyncio.Event()
            events = []

            async def handler(event):
                events.append(event)
                received.set()

            await bus.subscribe("engine_alert", handler)
            event_id = await bus.publish(
                "engine_alert",
                {"service": "api", "status": "degraded"},
                source="caller-controlled-label",
                workspace_id="workspace-a",
                producer_capability=capability,
            )
            assert event_id
            await asyncio.wait_for(received.wait(), timeout=1)
            proof = events[0]["provenance"]
            assert proof is not None
            assert proof.producer.producer_id == "health-monitor"
            assert proof.actor.subject_id == "user-1"
            assert proof.workspace_id == "workspace-a"
            assert events[0]["source"] == "caller-controlled-label"
            assert "provenance" not in events[0]["payload"]
            await bus.stop()
        finally:
            auth_identity_var.reset(token)

    asyncio.run(run())


def test_legacy_source_label_never_creates_verified_provenance():
    async def run():
        registry = ProducerRegistry({"health-monitor": "internal_health_producer"})
        bus = UnifiedEventBus(producer_registry=registry)
        received = asyncio.Event()
        events = []

        async def handler(event):
            events.append(event)
            received.set()

        await bus.subscribe("engine_alert", handler)
        event_id = await bus.publish(
            "engine_alert",
            {"service": "api", "status": "degraded"},
            source="health-monitor",
            workspace_id="workspace-a",
        )
        assert event_id
        await asyncio.wait_for(received.wait(), timeout=1)
        assert events[0]["provenance"] is None
        await bus.stop()

    asyncio.run(run())


def test_forged_capability_is_rejected_without_dispatch():
    async def run():
        registry = ProducerRegistry({"health-monitor": "internal_health_producer"})
        bus = UnifiedEventBus(producer_registry=registry)
        received = []

        async def handler(event):
            received.append(event)

        await bus.subscribe("engine_alert", handler)
        event_id = await bus.publish(
            "engine_alert",
            {"service": "api", "status": "degraded"},
            source="health-monitor",
            workspace_id="workspace-a",
            producer_capability="health-monitor",
        )
        assert event_id is None
        assert received == []
        await bus.stop()

    asyncio.run(run())


def test_capability_cannot_be_used_when_bus_has_no_registry():
    async def run():
        bus = UnifiedEventBus()
        event_id = await bus.publish(
            "engine_alert",
            {"service": "api", "status": "degraded"},
            workspace_id="workspace-a",
            producer_capability=object(),
        )
        assert event_id is None
        await bus.stop()

    asyncio.run(run())
