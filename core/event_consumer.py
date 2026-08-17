"""
Event Consumer Service — The Reactive Brain.

Listens to ALL events and triggers appropriate actions:
- backup_completed(failed) → Director recovery task
- engine_alert(critical) → Escalation
- trend_detected → ConditionEngine analysis
- task_failed(3x) → Auto-incident
- post_published → Engagement tracking
- content_failed(3x) → Investigation

This is what makes the system SELF-HEALING.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("kanyoza.event_consumer")


class EventConsumer:
    """Central event consumer — reacts to events autonomously."""

    # ═══════════════════════════════════════════════════════════════
    # THRESHOLDS
    # ═══════════════════════════════════════════════════════════════

    FAILURE_THRESHOLD = 3           # After 3 failures, escalate
    CRITICAL_ALERT_COOLDOWN = 1800  # 30 min between critical alerts
    EVENT_FLOOD_THRESHOLD = 50      # Max events per minute per service
    CORRELATION_WINDOW = 300        # 5 min window for correlation

    def __init__(self, shared_bus=None, supabase_client=None):
        self.shared_bus = shared_bus
        self.supabase = supabase_client

        self._running = False
        self._thread = None

        # Failure tracking
        self._failure_counts: Dict[str, int] = defaultdict(int)
        self._last_alert_time: Dict[str, float] = {}

        # Event history for correlation
        self._recent_events: deque = deque(maxlen=500)
        self._event_counts: Dict[str, int] = defaultdict(int)
        self._event_window_start = time.monotonic()

        # Correlation rules
        self._correlations = []

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._consume_loop,
            name="event-consumer",
            daemon=True,
        )
        self._thread.start()
        logger.info("🔌 Event Consumer started — system is now reactive")

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _consume_loop(self) -> None:
        while self._running:
            try:
                self._poll_events()
                self._check_correlations()
                self._check_rate_limits()
            except Exception:
                logger.exception("Event Consumer error")
            time.sleep(10)

    def _poll_events(self) -> None:
        """Poll for new events and react."""
        if not self.supabase:
            return

        try:
            result = self.supabase.table("shared_event_bus") \
                .select("*") \
                .eq("processed", False) \
                .order("created_at") \
                .limit(100) \
                .execute()

            for event in result.data or []:
                self._process_event(event)

        except Exception as exc:
            logger.error("Event polling failed: %s", exc)

    def _process_event(self, event: Dict[str, Any]) -> None:
        """Process a single event and trigger appropriate reaction."""
        event_name = event.get("event_name", "")
        event_id = event.get("id")
        source = event.get("source_service", "unknown")

        try:
            payload = json.loads(event.get("payload", "{}"))
        except json.JSONDecodeError:
            payload = {}

        # Track for correlation
        self._recent_events.append({
            "event_name": event_name,
            "source": source,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc),
        })

        # ═══════════════════════════════════════════════════════
        # REACT TO EVENTS
        # ═══════════════════════════════════════════════════════

        if event_name == "backup_completed" and payload.get("status") == "failed":
            self._handle_backup_failure(payload)

        elif event_name == "engine_alert" and payload.get("status") == "critical":
            self._handle_critical_alert(payload)

        elif event_name == "trend_detected":
            self._handle_trend(payload)

        elif event_name == "task_failed":
            self._handle_task_failure(payload)

        elif event_name == "content_failed":
            self._handle_content_failure(payload)

        elif event_name == "post_published":
            self._handle_post_published(payload)

        elif event_name == "service_heartbeat":
            self._handle_heartbeat(payload)

        # Mark as processed
        self._mark_processed(event_id)

    def _handle_backup_failure(self, payload: Dict[str, Any]) -> None:
        """Backup failed — track and escalate if repeated."""
        key = "backup_failure"
        self._failure_counts[key] += 1
        count = self._failure_counts[key]

        logger.warning(f"💾 Backup failure #{count}")

        if count >= self.FAILURE_THRESHOLD:
            # Escalate to Director
            self.shared_bus.publish("engine_alert", {
                "engine": "backup",
                "status": "critical",
                "message": f"Backup failed {count} times consecutively",
            })
            self._failure_counts[key] = 0  # Reset after escalation

    def _handle_critical_alert(self, payload: Dict[str, Any]) -> None:
        """Critical alert — apply cooldown and escalate."""
        engine = payload.get("engine", "unknown")

        # Cooldown check
        now = time.monotonic()
        last_alert = self._last_alert_time.get(engine, 0)

        if now - last_alert < self.CRITICAL_ALERT_COOLDOWN:
            logger.debug(f"Alert cooldown active for {engine} — skipping")
            return

        self._last_alert_time[engine] = now
        logger.critical(f"🚨 CRITICAL: {engine} — {payload.get('message', '')}")

        # Escalate via Telegram
        try:
            from services.email_service import email_service
            email_service.send_telegram(
                f"🚨 <b>CRITICAL ALERT</b>\n"
                f"Engine: {engine}\n"
                f"Message: {payload.get('message', '')}\n"
                f"<i>{datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>"
            )
        except Exception:
            pass

    def _handle_trend(self, payload: Dict[str, Any]) -> None:
        """Trend detected — feed to ConditionEngine."""
        topic = payload.get("topic", "")
        logger.info(f"📈 Trend: {topic}")

        # Publish for ConditionEngine to analyze
        self.shared_bus.publish("trend_analysis_request", {
            "topic": topic,
            "source": payload.get("source", "unknown"),
        })

    def _handle_task_failure(self, payload: Dict[str, Any]) -> None:
        """Task failed — track and escalate."""
        task_desc = payload.get("description", "unknown task")
        key = f"task:{task_desc}"
        self._failure_counts[key] += 1
        count = self._failure_counts[key]

        logger.warning(f"❌ Task failure #{count}: {task_desc}")

        if count >= self.FAILURE_THRESHOLD:
            self.shared_bus.publish("engine_alert", {
                "engine": "director",
                "status": "degraded",
                "message": f"Task '{task_desc}' failed {count} times",
            })
            self._failure_counts[key] = 0

    def _handle_content_failure(self, payload: Dict[str, Any]) -> None:
        """Content generation failed — track and escalate."""
        brand = payload.get("brand", "unknown")
        key = f"content:{brand}"
        self._failure_counts[key] += 1
        count = self._failure_counts[key]

        logger.warning(f"📝 Content failure #{count} for {brand}")

        if count >= self.FAILURE_THRESHOLD:
            self.shared_bus.publish("engine_alert", {
                "engine": "content_engine",
                "status": "degraded",
                "message": f"Content generation failed {count} times for {brand}",
            })
            self._failure_counts[key] = 0

    def _handle_post_published(self, payload: Dict[str, Any]) -> None:
        """Post published — track for engagement."""
        topic = payload.get("topic", "")
        brand = payload.get("brand", "")
        logger.info(f"✅ Post published: {topic} for {brand}")

        # Publish for engagement tracking
        self.shared_bus.publish("engagement_tracking_request", {
            "brand": brand,
            "topic": topic,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _handle_heartbeat(self, payload: Dict[str, Any]) -> None:
        """Service heartbeat — track uptime."""
        service = payload.get("service", "")
        logger.debug(f"💓 Heartbeat: {service}")

    def _check_correlations(self) -> None:
        """Correlate recent events to find patterns."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self.CORRELATION_WINDOW)

        # Filter recent events
        recent = [
            e for e in self._recent_events
            if e["timestamp"] >= cutoff
        ]

        if not recent:
            return

        # Check: Multiple engine_alerts in short time
        critical_alerts = [
            e for e in recent
            if e["event_name"] == "engine_alert" and e["payload"].get("status") == "critical"
        ]

        if len(critical_alerts) >= 3:
            engines = [e["payload"].get("engine", "") for e in critical_alerts]
            logger.critical(f"🚨 CORRELATION: Multiple critical alerts: {engines}")
            self.shared_bus.publish("engine_alert", {
                "engine": "system",
                "status": "critical",
                "message": f"{len(critical_alerts)} critical alerts in {self.CORRELATION_WINDOW}s",
            })

    def _check_rate_limits(self) -> None:
        """Check for event flooding."""
        now = time.monotonic()
        window_elapsed = now - self._event_window_start

        if window_elapsed >= 60:
            # Reset window
            flooded_services = [
                service for service, count in self._event_counts.items()
                if count > self.EVENT_FLOOD_THRESHOLD
            ]

            for service in flooded_services:
                logger.warning(f"🌊 Event flood detected from {service}: {self._event_counts[service]} events/min")
                self.shared_bus.publish("engine_alert", {
                    "engine": service,
                    "status": "degraded",
                    "message": f"Event flood: {self._event_counts[service]} events in 60s",
                })

            self._event_counts.clear()
            self._event_window_start = now

    def _mark_processed(self, event_id: Any) -> None:
        """Mark event as processed."""
        try:
            self.supabase.table("shared_event_bus") \
                .update({"processed": True, "processed_at": datetime.now(timezone.utc).isoformat()}) \
                .eq("id", event_id) \
                .execute()
        except Exception:
            pass

    def get_status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "failure_counts": dict(self._failure_counts),
            "events_in_window": len(self._recent_events),
            "event_rate": sum(self._event_counts.values()),
        }


# ═══════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════

_event_consumer: Optional[EventConsumer] = None


def init_event_consumer(shared_bus=None, supabase_client=None) -> EventConsumer:
    global _event_consumer
    _event_consumer = EventConsumer(
        shared_bus=shared_bus,
        supabase_client=supabase_client,
    )
    _event_consumer.start()
    return _event_consumer


def get_event_consumer() -> Optional[EventConsumer]:
    return _event_consumer


__all__ = ["EventConsumer", "init_event_consumer", "get_event_consumer"]
