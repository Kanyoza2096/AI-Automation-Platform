"""
HealCommandConsumer — converts OODA `heal_command` events into governed commands.

OODA publishes heal_command when it detects a degraded service. Without this
consumer, the event is a no-op. With it, the event becomes a real Action Kernel
transaction that requires operator approval before executing.

Approval tier: operator. Restarting a service is reversible but impactful;
the human should approve before the command runs.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.unified_bus import UnifiedEventBus

logger = logging.getLogger("kanyoza.workforce.consumers.heal_command")


# Map engine names to the governed command they should trigger.
# Keys are the "engine" field from the heal_command payload.
HEAL_COMMANDS: Dict[str, str] = {
    "job_stats_service":    "service.restart",
    "scheduler":            "scheduler.restart",
    "dream_system":         "dream_system.restart",
    "autonomous_loop":      "autonomous_loop.restart",
    "meta_engine":          "service.restart",
    "director":             "service.restart",
    "condition_engine":     "service.restart",
    "redis":                "service.restart",
    "supabase":             "service.restart",
}


class HealCommandConsumer:
    def __init__(self, bus: UnifiedEventBus, container: Any = None):
        self._bus = bus
        self._container = container
        self._subscribed = False
        self._events_handled = 0
        self._events_failed = 0

    async def start(self) -> None:
        if self._subscribed:
            return
        try:
            await self._bus.subscribe("heal_command", self._on_heal_command)
            self._subscribed = True
            logger.info("HealCommandConsumer started")
        except Exception as exc:
            logger.error("HealCommandConsumer subscribe failed: %s", exc)

    async def _on_heal_command(self, event: Any = None, payload: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        try:
            raw = payload if isinstance(payload, dict) else (event if isinstance(event, dict) else {})
            nested = raw.get("payload") if isinstance(raw, dict) else None
            # UnifiedEventBus handlers receive an envelope, not the raw payload.
            # Preserve envelope correlation metadata while preferring domain fields.
            data = {**raw, **nested} if isinstance(nested, dict) else raw
            if not data:
                return

            engine = str(data.get("engine", "")).strip()
            reason = str(data.get("reason", "OODA proactive heal")).strip()
            workspace_id = str(data.get("workspace_id", "platform")).strip()

            if not engine:
                logger.warning("heal_command missing engine — skipping")
                return

            # Look up the correct governed command for this engine.
            command_id = HEAL_COMMANDS.get(engine, "service.restart")

            # Route the command through Action Kernel. The kernel enforces
            # approval tier and creates a pending transaction.
            result = await self._dispatch(command_id, {
                "target": engine,
                "reason": reason,
                "workspace_id": workspace_id,
                "correlation_id": str(data.get("correlation_id") or ""),
                "requested_by": "autonomous_loop",
            })

            self._events_handled += 1
            logger.info(
                "heal_command dispatched: engine=%s command=%s transaction=%s",
                engine, command_id, result.get("transaction_id", "?"),
            )
        except Exception as exc:
            self._events_failed += 1
            logger.exception("heal_command handling failed: %s", exc)

    async def _dispatch(self, command_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch via the existing governed command pipeline."""
        try:
            from api.v1 import dispatch_governed_command  # or whatever module owns it
        except ImportError:
            # Fallback: call the commands endpoint directly via HTTP if the
            # in-process dispatch isn't available.
            try:
                import httpx
                from config.settings import settings
                async with httpx.AsyncClient(base_url=settings.python_core_base_url if hasattr(settings, "python_core_base_url") else "http://127.0.0.1:10000") as client:
                    resp = await client.post(
                        f"/api/v1/commands/{command_id}",
                        json=body,
                        headers={"X-API-Key": getattr(settings, "master_api_token", "")},
                    )
                    return resp.json()
            except Exception as exc:
                logger.warning("HTTP fallback failed: %s", exc)
                return {"ok": False, "error": str(exc)}

        try:
            # Preferred: use the internal dispatch which does not require HTTP.
            result = await dispatch_governed_command(command_id, body)
            return result if isinstance(result, dict) else {"ok": False, "error": "unexpected_result"}
        except Exception as exc:
            logger.warning("Governed command dispatch failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def get_status(self) -> Dict[str, Any]:
        return {
            "consumer": "heal_command",
            "subscribed": self._subscribed,
            "events_handled": self._events_handled,
            "events_failed": self._events_failed,
            "supported_engines": list(HEAL_COMMANDS.keys()),
        }
