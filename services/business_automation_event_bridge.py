"""EventBus -> Business Automation bridge.

This bridge makes automation event-reactive without changing the authority model:
EventBus delivers an authenticated domain event, BusinessAutomationEngine matches
only explicitly subscribed rules, and the existing execution/governance boundaries
remain responsible for consequential actions.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Dict, Optional

logger = logging.getLogger("kanyoza.business_automation.event_bridge")


class BusinessAutomationEventBridge:
    VERSION = "2.1.0"
    CONSUMER_GROUP = "business_automation"
    POLL_INTERVAL_SECONDS = 2.0
    CLAIM_BATCH_SIZE = 25
    FANOUT_READINESS_RPC = "business_automation_event_fanout_ready"
    RECOVERY_READINESS_RPC = "business_automation_stale_reservation_guard_ready"

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
        self._fanout_ready = False
        self._recovery_guard_ready = False
        self._durable_claimed = 0
        self._durable_processed = 0
        self._durable_failures = 0

    def set_canonical_router(self, router) -> None:
        self._canonical_router = router
        logger.info("BusinessAutomationEventBridge canonical V71.14 router installed")

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
        readiness = await self._check_durable_fanout() if db is not None else {}
        self._fanout_ready = readiness.get("fanout", False)
        self._recovery_guard_ready = readiness.get("recovery_guard", False)
        # A database connection alone is not evidence that the producer-side
        # fan-out exists. Do not advertise or poll an inbox that cannot receive
        # new deliveries; the low-latency UnifiedEventBus path remains available.
        self._durable_enabled = self._fanout_ready and self._recovery_guard_ready
        if self._durable_enabled:
            self._durable_task = asyncio.create_task(
                self._durable_loop(),
                name="business-automation-event-inbox",
            )
        else:
            logger.error(
                "BusinessAutomation durable inbox unavailable: "
                "fan-out and stale-reservation safeguards are not both confirmed; "
                "live event path remains active"
            )
        logger.info(
            "BusinessAutomationEventBridge v%s wired durable_inbox=%s fanout_ready=%s recovery_guard_ready=%s",
            self.VERSION,
            self._durable_enabled,
            self._fanout_ready,
            self._recovery_guard_ready,
        )

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
        self._durable_enabled = False
        self._fanout_ready = False
        self._recovery_guard_ready = False
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

    @staticmethod
    def _is_transport_failure(exc: Exception) -> bool:
        names = {
            "ConnectError", "ConnectTimeout", "ReadError", "ReadTimeout",
            "WriteError", "WriteTimeout", "PoolTimeout", "RemoteProtocolError",
            "LocalProtocolError", "NetworkError", "ConnectionError",
            "BrokenPipeError", "ConnectionResetError", "ConnectionAbortedError",
        }
        if exc.__class__.__name__ in names:
            return True
        text = str(exc).lower()
        return any(token in text for token in (
            "broken pipe", "connectionterminated", "connection terminated",
            "remoteprotocolerror", "connection reset", "connection aborted",
            "connection refused", "connection pool", "transport",
        ))

    async def _refresh_db_client(self) -> bool:
        """Replace a stale Supabase transport without changing authority semantics."""
        try:
            from services.supabase import create_fresh_service_client
            client = await asyncio.to_thread(create_fresh_service_client)
            if client is not None and hasattr(self.engine, "set_supabase_client"):
                self.engine.set_supabase_client(client)
                logger.info("BusinessAutomation DB: refreshed Supabase transport after connection failure")
                return True
        except Exception as exc:
            logger.warning("BusinessAutomation DB: transport refresh failed: %s", str(exc)[:200])
        return False

    async def _execute_db_rpc(self, name: str, args: dict[str, Any], *, max_retries: int = 2):
        """Execute an inbox RPC with bounded transport recovery.

        A failed claim/ack remains safe to retry because claim uses a lease and
        completion is keyed by delivery id + claim token. No business action is
        retried here; only the database transport operation is retried.
        """
        for attempt in range(max_retries + 1):
            db = getattr(self.engine, "_get_db", lambda: None)()
            if db is None:
                return None
            try:
                return await asyncio.to_thread(lambda db=db: db.rpc(name, args).execute())
            except Exception as exc:
                if attempt >= max_retries:
                    raise
                if self._is_transport_failure(exc):
                    await self._refresh_db_client()
                delay = min(1.0 * (2 ** attempt), 10.0) * random.uniform(0.75, 1.25)
                logger.warning(
                    "BusinessAutomation DB RPC '%s' attempt %d/%d failed (%s); retrying in %.1fs",
                    name, attempt + 1, max_retries,
                    "transport" if self._is_transport_failure(exc) else "retryable", delay,
                )
                await asyncio.sleep(delay)
        return None

    async def _check_durable_fanout(self) -> dict[str, bool]:
        """Require both producer fan-out and stale-reservation recovery safeguards."""
        readiness = {"fanout": False, "recovery_guard": False}
        checks = (
            ("fanout", self.FANOUT_READINESS_RPC),
            ("recovery_guard", self.RECOVERY_READINESS_RPC),
        )
        for key, rpc_name in checks:
            try:
                result = await self._execute_db_rpc(rpc_name, {}, max_retries=0)
                readiness[key] = bool(result is not None and result.data is True)
            except Exception as exc:
                logger.warning(
                    "BusinessAutomation readiness check '%s' failed: %s",
                    rpc_name,
                    str(exc)[:200],
                )
        return readiness

    async def _claim_durable_events(self) -> list[dict[str, Any]]:
        result = await self._execute_db_rpc(
            "claim_business_automation_event_deliveries",
            {"p_consumer_group": self.CONSUMER_GROUP, "p_limit": self.CLAIM_BATCH_SIZE, "p_lease_seconds": 120},
        )
        return list(result.data or []) if result is not None else []

    async def _admit(self, *, event_name: str, event: dict[str, Any], workspace_id: str, approved: bool = False):
        # V71.14: if the production composition root installed the canonical
        # event router, all business-event execution admission goes through it.
        # Tests/legacy isolated consumers may still use the existing engine.
        router = getattr(self, "_canonical_router", None)
        if router is not None:
            return await router.admit_event(event_name=event_name, event=event, workspace_id=workspace_id, approved=approved)
        return await self.engine.run_event(event_name=event_name, event=event, workspace_id=workspace_id, approved=approved)

    async def _process_durable_row(self, row: dict[str, Any]) -> None:
        event_name = str(row.get("event_type") or "").strip(); workspace_id = str(row.get("workspace_id") or "").strip(); event_id = str(row.get("event_id") or "").strip()
        if not event_name or not workspace_id or not event_id:
            await self._ack_durable(row, success=False, error="invalid durable event envelope"); return
        payload = row.get("payload") or {}; payload = payload if isinstance(payload, dict) else {}
        event = dict(payload); event.update({"event_id": event_id, "event_name": event_name, "workspace_id": workspace_id})
        for key in ("brand_id", "decision_id", "correlation_id", "parent_event_id"):
            if row.get(key) is not None: event[key] = row[key]
        try:
            executions = await self._admit(event_name=event_name, event=event, workspace_id=workspace_id, approved=False)
            if executions: self._events_routed += 1; self._executions += len(executions)
            await self._ack_durable(row, success=True)
        except Exception as exc:
            await self._ack_durable(row, success=False, error=str(exc)); raise

    async def _ack_durable(self, row: dict[str, Any], *, success: bool, error: str | None = None) -> None:
        db = getattr(self.engine, "_get_db", lambda: None)(); delivery_id = str(row.get("delivery_id") or row.get("id") or "").strip()
        claim_token = str(row.get("claim_token") or row.get("claimed_by") or "").strip()
        if not delivery_id: return
        if not claim_token:
            raise RuntimeError("durable business automation delivery is missing claim token")
        if db is None: return
        result = await self._execute_db_rpc(
            "complete_business_automation_event_delivery",
            {"p_delivery_id": delivery_id, "p_workspace_id": str(row.get("workspace_id") or "").strip(), "p_consumer_group": self.CONSUMER_GROUP, "p_claim_token": claim_token, "p_success": success, "p_error": (error or "")[:1000]},
        )
        if result is None or result.data is False or result.data == []:
            raise RuntimeError("durable business automation delivery completion lost ownership")

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
        event_id = str(envelope.get("event_id") or event.get("event_id") or "").strip()
        # Never admit an event without a durable identity; Business Automation
        # uses event_id as its replay fence. Missing identity is rejected rather
        # than converted into a shared `missing` idempotency key.
        if not event_id:
            self._errors += 1
            logger.warning("rejecting event without event_id event=%s workspace=%s", event_name, workspace_id)
            return
        event["event_id"] = event_id
        event["event_name"] = event_name
        event["workspace_id"] = workspace_id
        if envelope.get("brand_id") is not None:
            event["brand_id"] = envelope.get("brand_id")
        if envelope.get("decision_id") is not None:
            event["decision_id"] = envelope.get("decision_id")
        if envelope.get("correlation_id") is not None:
            event["correlation_id"] = envelope.get("correlation_id")

        try:
            executions = await self._admit(
                event_name=event_name, event=event, workspace_id=workspace_id, approved=False
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
            "durable_inbox": {
                "enabled": self._durable_enabled,
                "fanout_ready": self._fanout_ready,
                "recovery_guard_ready": self._recovery_guard_ready,
                "claimed": self._durable_claimed,
                "processed": self._durable_processed,
                "failures": self._durable_failures,
            },
        }


business_automation_event_bridge = BusinessAutomationEventBridge()
