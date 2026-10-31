"""
Consumer registry — one function to start all OODA consumers on boot.

Usage in main.py lifespan:
    from services.workforce.consumers import start_all_consumers
    consumers = await start_all_consumers(app, container)
    app.state.ooda_consumers = consumers
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger("kanyoza.workforce.consumers")


async def start_all_consumers(app: Any, container: Any) -> List[Any]:
    """Start all OODA consumers. Returns the list of started consumers."""
    consumers: List[Any] = []

    # Resolve dependencies
    bus = None
    sb = None
    try:
        from core.unified_bus import UnifiedEventBus
        bus = container.try_resolve(UnifiedEventBus)
    except Exception as exc:
        logger.warning("UnifiedEventBus unavailable: %s", exc)

    try:
        from services.supabase import supabase_service
        sb = supabase_service.service_client or supabase_service.client
    except Exception:
        pass

    if bus is None:
        logger.error("Cannot start consumers — no event bus available")
        return consumers

    try:
        from services.workforce.consumers.heal_command_consumer import HealCommandConsumer
        c = HealCommandConsumer(bus=bus, container=container)
        await c.start()
        consumers.append(c)
    except Exception as exc:
        logger.error("HealCommandConsumer start failed: %s", exc)

    try:
        from services.workforce.consumers.escalation_consumer import EscalationConsumer
        c = EscalationConsumer(bus=bus, supabase_client=sb)
        await c.start()
        consumers.append(c)
    except Exception as exc:
        logger.error("EscalationConsumer start failed: %s", exc)

    try:
        from services.workforce.consumers.prediction_consumer import PredictionAlertConsumer
        c = PredictionAlertConsumer(bus=bus, supabase_client=sb)
        await c.start()
        consumers.append(c)
    except Exception as exc:
        logger.error("PredictionAlertConsumer start failed: %s", exc)

    logger.info("Started %d OODA consumers", len(consumers))
    return consumers


def get_consumer_status(consumers: List[Any]) -> List[Dict[str, Any]]:
    return [c.get_status() for c in consumers if hasattr(c, "get_status")]
