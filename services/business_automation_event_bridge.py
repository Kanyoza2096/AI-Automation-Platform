"""EventBus -> Business Automation bridge.

This bridge makes automation event-reactive without changing the authority model:
EventBus delivers an authenticated domain event, BusinessAutomationEngine matches
only explicitly subscribed rules, and the existing execution/governance boundaries
remain responsible for consequential actions.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("kanyoza.business_automation.event_bridge")


class BusinessAutomationEventBridge:
    VERSION = "2.0.0"
    CONSUMER_GROUP = "business_automation"
    POLL_INTERVAL_SECONDS = 2.0
    CLAIM_BATCH_SIZE = 25

    def __init__(self, engine=None, event_bus=None):
        self.engine = engine
        self.event_bus = event_bus
        self._unsubscribe = None
        self._wired = False
        self._events_seen = 0
        self._events_routed = 0
        self._executions = 0
        self._errors = 0
        self._durable_task = None
        self._durable_enabled = False
        self._durable_claimed = 0
        self._durable_processed = 0
        self._durable_failures = 0

    async def wire(self, event_bus=None) -> None:
        if self._wired:
            return
        if event_bus is not None:
            self.event_bus = event_bus
        if self.engine is None:
            from services.business_automation import business_automation_engine
            self.engine = business_automation_engine
        if self.event_bus is None:
            raise RuntimeError("UnifiedEventBus is required")
        self._unsubscribe = await self.event_bus.subscribe_all(self._handle)
        self._wired = True
        db = getattr(self.engine, "_get_db", lambda: None)()
        self._durable_enabled = db is not None
        if self._durable_enabled:
            self._durable_task = asyncio.create_task(self._durable_loop(), name="business-automation-event-inbox")
        logger.info("BusinessAutomationEventBridge v%s wired durable_inbox=%s", self.VERSION, self._durable_enabled)

    async def unwire(self) -> None:
        if self._unsubscribe:
            try:
                self._unsubscribe()
            except Exception:
                logger.exception("failed to unsubscribe business automation bridge")
        self._unsubscribe = None
        if self._durable_task and not self._durable_task.done():
            self._durable_task.cancel()
            try:
                await self._durable_task
            except asyncio.CancelledError:
                pass
        self._durable_task = None
        self._wired = False

    async def _durable_loop(self) -> None:
        while self._wired:
            try:
                rows = await self._claim_durable_events()
                if not rows:
                    await asyncio.sleep(self.POLL_INTERVAL_SECONDS); continue
                for row in rows:
                    self._durable_claimed += 1
                    try:
                        await self._process_durable_row(row); self._durable_processed += 1
                    except Exception:
                        self._durable_failures += 1; logger.exception("durable business automation event failed")
            except asyncio.CancelledError: break
            except Exception:
                self._durable_failures += 1; logger.exception("durable business automation inbox error")
                await asyncio.sleep(self.POLL_INTERVAL_SECONDS)

    async def _claim_durable_events(self) -> list[dict[str, Any]]:
        db = getattr(self.engine, "_get_db", lambda: None)()
        if db is None: return []
        result = await asyncio.to_thread(lambda: db.rpc("claim_business_automation_event_deliveries", {"p_consumer_group": self.CONSUMER_GROUP, "p_limit": self.CLAIM_BATCH_SIZE, "p_lease_seconds": 120}).execute())
        return list(result.data or [])

    async def _process_durable_row(self, row: dict[str, Any]) -> None:
        event_name = str(row.get("event_type") or "").strip(); workspace_id = str(row.get("workspace_id") or "").strip(); event_id = str(row.get("event_id") or "").strip()
        if not event_name or not workspace_id or not event_id:
            await self._ack_durable(row, success=False, error="invalid durable event envelope"); return
        payload = row.get("payload") or {}; payload = payload if isinstance(payload, dict) else {}
        event = dict(payload); event.update({"event_id": event_id, "event_name": event_name, "workspace_id": workspace_id})
        for key in ("brand_id", "decision_id", "correlation_id", "parent_event_id"):
            if row.get(key) is not None: event[key] = row[key]
        try:
            executions = await self.engine.run_event(event_name=event_name, event=event, workspace_id=workspace_id, approved=False)
            if executions: self._events_routed += 1; self._executions += len(executions)
            await self._ack_durable(row, success=True)
        except Exception as exc:
            await self._ack_durable(row, success=False, error=str(exc)); raise

    async def _ack_durable(self, row: dict[str, Any], *, success: bool, error: str | None = None) -> None:
        db = getattr(self.engine, "_get_db", lambda: None)(); delivery_id = str(row.get("delivery_id") or row.get("id") or "").strip()
        if db is None or not delivery_id: return
        await asyncio.to_thread(lambda: db.rpc("complete_business_automation_event_delivery", {"p_delivery_id": delivery_id, "p_consumer_group": self.CONSUMER_GROUP, "p_success": success, "p_error": (error or "")[:1000]}).execute())

    async def _handle(self, envelope: Dict[str, Any]) -> None:
        self._events_seen += 1
        event_name = str(envelope.get("event_name") or "").strip()
        source = str(envelope.get("source") or "").strip()
        if not event_name or event_name.startswith("business_automation.") or source == "business_automation":
            return

        payload = envelope.get("payload") or {}
        if not isinstance(payload, dict):
            return
        workspace_id = str(envelope.get("workspace_id") or payload.get("workspace_id") or "").strip()
        if not workspace_id or workspace_id == "platform":
            return

        event = dict(payload)
        event["event_id"] = str(envelope.get("event_id") or event.get("event_id") or "")
        event["event_name"] = event_name
        event["workspace_id"] = workspace_id
        if envelope.get("brand_id") is not None:
            event["brand_id"] = envelope.get("brand_id")
        if envelope.get("decision_id") is not None:
            event["decision_id"] = envelope.get("decision_id")
        if envelope.get("correlation_id") is not None:
            event["correlation_id"] = envelope.get("correlation_id")

        try:
            executions = await self.engine.run_event(
                event_name=event_name,
                event=event,
                workspace_id=workspace_id,
                approved=False,
            )
            if executions:
                self._events_routed += 1
                self._executions += len(executions)
        except Exception as exc:
            self._errors += 1
            logger.exception("event-driven automation failed event=%s workspace=%s: %s", event_name, workspace_id, exc)

    def stats(self) -> Dict[str, Any]:
        return {
            "version": self.VERSION,
            "wired": self._wired,
            "events_seen": self._events_seen,
            "events_routed": self._events_routed,
            "executions": self._executions,
            "errors": self._errors,
            "durable_inbox": {"enabled": self._durable_enabled, "claimed": self._durable_claimed, "processed": self._durable_processed, "failures": self._durable_failures},
        }


business_automation_event_bridge = BusinessAutomationEventBridge()
