"""
EscalationConsumer — converts OODA `escalation_request` events into operator tasks.

OODA publishes escalation_request when it detects a critical condition it
cannot safely auto-remediate. Without this consumer, the request is lost in
the bus. With it, the operator gets a durable task in the queue with context.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.unified_bus import UnifiedEventBus

logger = logging.getLogger("kanyoza.workforce.consumers.escalation")


class EscalationConsumer:
    def __init__(self, bus: UnifiedEventBus, supabase_client: Any = None):
        self._bus = bus
        self._sb = supabase_client
        self._subscribed = False
        self._events_handled = 0
        self._events_failed = 0

    async def start(self) -> None:
        if self._subscribed:
            return
        try:
            await self._bus.subscribe("escalation_request", self._on_escalation)
            self._subscribed = True
            logger.info("EscalationConsumer started")
        except Exception as exc:
            logger.error("EscalationConsumer subscribe failed: %s", exc)

    async def _on_escalation(self, event: Any = None, payload: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        try:
            raw = payload if isinstance(payload, dict) else (event if isinstance(event, dict) else {})
            nested = raw.get("payload") if isinstance(raw, dict) else None
            # UnifiedEventBus supplies an envelope with the domain payload nested
            # under "payload". Merge it over the envelope so engine and reason
            # are preserved while correlation/workspace metadata remains available.
            data = {**raw, **nested} if isinstance(nested, dict) else raw
            if not data:
                return

            engine = str(data.get("engine", "unknown")).strip()
            reason = str(data.get("reason", "")).strip()
            workspace_id = str(data.get("workspace_id", "platform")).strip()
            correlation_id = str(data.get("correlation_id", uuid.uuid4().hex))

            task = {
                "id": f"task_{uuid.uuid4().hex[:12]}",
                "kind": "operator_escalation",
                "title": f"Escalation: {engine} requires operator attention",
                "description": reason or f"{engine} reported a condition requiring human review",
                "severity": "high",
                "status": "pending",
                "workspace_id": workspace_id,
                "correlation_id": correlation_id,
                "source": "autonomous_loop",
                "context": data,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            # Persist to the operator task queue
            if self._sb is not None:
                try:
                    await asyncio.to_thread(
                        lambda: self._sb.table("operator_tasks").insert(task).execute()
                    )
                except Exception as exc:
                    logger.warning("Escalation persist failed: %s", exc)

            self._events_handled += 1
            logger.warning(
                "escalation_request created operator task: engine=%s task=%s",
                engine, task["id"],
            )

            # Also publish on the bus so any real-time UI can pick it up
            try:
                await self._bus.publish("operator.task.created", {
                    "task_id": task["id"],
                    "title": task["title"],
                    "severity": task["severity"],
                    "workspace_id": workspace_id,
                    "engine": engine,
                    "correlation_id": correlation_id,
                }, source="escalation_consumer", correlation_id=correlation_id)
            except Exception:
                pass
        except Exception as exc:
            self._events_failed += 1
            logger.exception("escalation handling failed: %s", exc)

    def get_status(self) -> Dict[str, Any]:
        return {
            "consumer": "escalation",
            "subscribed": self._subscribed,
            "events_handled": self._events_handled,
            "events_failed": self._events_failed,
        }


# Need this import for asyncio.to_thread
import asyncio
