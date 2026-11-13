"""Composition-root adapters for application use cases.

HTTP route modules consume application services through these factories rather
than constructing infrastructure implementations themselves. Infrastructure
composition remains outside the application package.
"""
from __future__ import annotations

from typing import Any

from application.action_intents import ActionIntentApplicationService
from application.website_integrations import WebsiteIntegrationApplicationService


def action_intent_application(request: Any) -> ActionIntentApplicationService:
    container = getattr(request.app.state, "container", None)
    db = getattr(getattr(container, "supabase_service", None), "client", None) if container else None
    from services.action_kernel import build_default_action_kernel
    return ActionIntentApplicationService(
        build_default_action_kernel(db=db, allow_in_memory=False)
    )


def website_integration_application(request: Any) -> WebsiteIntegrationApplicationService:
    container = getattr(request.app.state, "container", None)
    client = getattr(getattr(container, "supabase_service", None), "client", None)
    if client is None:
        try:
            from services.supabase import supabase_service
            client = getattr(supabase_service, "service_client", None) or getattr(supabase_service, "client", None)
        except Exception:
            client = None
    if client is None:
        raise RuntimeError("Durable database unavailable")

    from services.integrations import WebsiteIntegrationService
    from core.unified_bus import get_unified_bus

    class _BusPublisher:
        async def publish(self, event_type, *, payload, source, workspace_id):
            bus = await get_unified_bus()
            if bus is None:
                raise RuntimeError("Unified Event Bus is unavailable")
            published_id = await bus.publish(
                event_type,
                payload=payload,
                source=source,
                workspace_id=workspace_id,
            )
            if not published_id:
                raise RuntimeError("Unified Event Bus did not accept the website integration event")

    return WebsiteIntegrationApplicationService(
        WebsiteIntegrationService(client),
        _BusPublisher(),
    )
