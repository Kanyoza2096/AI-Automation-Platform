"""
Autonomous Loop — The Complete Closed-Loop System.

Implements the full OODA loop:
  Observe → Orient → Decide → Act → Learn → (repeat)

This is the "nervous system" that connects:
  - Sensory Layer (Plugins: TrendIntelligence, Monitor, GitHub, CRM)
  - MetaEngine (Overseer: pattern detection)
  - Director (CEO: decision-making)
  - ConditionEngine (Strategist: learning)
  - Execution Layer (Plugins: actions)
  - Learning Loop (outcome analysis)

The system self-drives: sees, thinks, decides, acts, learns — automatically.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("kanyoza.autonomous_loop")


class AutonomousLoop:
    """The complete closed-loop autonomous system coordinator."""

    def __init__(
        self,
        shared_bus=None,
        meta_engine=None,
        director=None,
        condition_engine=None,
        container=None,
    ):
        self.shared_bus = shared_bus
        self.meta_engine = meta_engine
        self.director = director
        self.condition_engine = condition_engine
        self.container = container

        self._running = False
        self._thread = None
        self._lock = threading.RLock()

        # Event routing table
        self._routing = {
            # Sensory → MetaEngine
            "trend_detected": self._handle_trend,
            "engine_status_update": self._handle_status_update,
            "service_heartbeat": self._handle_heartbeat,
            "backup_completed": self._handle_backup,
            "incident_report": self._handle_incident,

            # MetaEngine → Director
            "engine_alert": self._handle_alert,
            "anomaly_detected": self._handle_anomaly,

            # Director → Workers
            "publish_request": self._handle_publish_request,
            "task_created": self._handle_task_created,

            # Workers → ConditionEngine
            "post_published": self._handle_post_published,
            "content_failed": self._handle_content_failed,
            "engagement_data": self._handle_engagement,

            # ConditionEngine → Director
            "strategy_recommendation": self._handle_strategy,
            "strategy_executed": self._handle_strategy_executed,
            "brand_health_updated": self._handle_brand_health,

            # Learning
            "task_completed": self._handle_task_completed,
            "task_failed": self._handle_task_failed,
        }

    def start(self) -> None:
        """Start the autonomous loop."""
        with self._lock:
            if self._running:
                return
            self._running = True

        self._thread = threading.Thread(
            target=self._loop,
            name="autonomous-loop",
            daemon=True,
        )
        self._thread.start()
        logger.info("🔄 Autonomous Loop started — system is self-driving")

    def stop(self) -> None:
        """Stop the autonomous loop."""
        with self._lock:
            self._running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None
        logger.info("Autonomous Loop stopped")

    def _loop(self) -> None:
        """Main loop — processes events and coordinates services."""
        while self._running:
            try:
                self._tick()
            except Exception:
                logger.exception("Autonomous Loop error")
            time.sleep(15)  # Check every 15 seconds

    def _tick(self) -> None:
        """One iteration of the autonomous loop."""
        if not self.shared_bus:
            return

        # Poll for new events
        self.shared_bus.poll()

        # Check system health
        if self.meta_engine:
            snapshot = self.meta_engine._current_snapshot
            if snapshot and snapshot.overall_status.value in ("degraded", "critical"):
                self._publish_health_alert(snapshot)

    # ═══════════════════════════════════════════════════════════════
    # SENSORY → META ENGINE
    # ═══════════════════════════════════════════════════════════════

    def _handle_trend(self, payload: Dict[str, Any]) -> None:
        """Trend detected by plugin — send to Director for decision."""
        topic = payload.get("topic", "")
        source = payload.get("source", "")

        logger.info(f"🔍 SENSORY: Trend detected — '{topic}' from {source}")

        # Tell Director about the trend
        if self.shared_bus:
            self.shared_bus.publish("trend_analysis_request", {
                "topic": topic,
                "source": source,
                "action_required": True,
            })

    def _handle_status_update(self, payload: Dict[str, Any]) -> None:
        """Engine status changed — notify MetaEngine."""
        engine = payload.get("engine", "")
        status = payload.get("status", "")

        logger.debug(f"🔍 STATUS: {engine} is {status}")

        if self.meta_engine:
            try:
                self.meta_engine.record_metric(
                    engine,
                    "status",
                    1.0 if status == "healthy" else 0.0,
                )
            except Exception:
                pass

    def _handle_heartbeat(self, payload: Dict[str, Any]) -> None:
        """Service heartbeat — track uptime."""
        service = payload.get("service", "")
        logger.debug(f"💓 HEARTBEAT: {service}")

    def _handle_backup(self, payload: Dict[str, Any]) -> None:
        """Backup completed."""
        status = payload.get("status", "")
        logger.info(f"💾 BACKUP: {status}")

    def _handle_incident(self, payload: Dict[str, Any]) -> None:
        """Incident reported."""
        incident = payload.get("incident", "")
        logger.warning(f"🚨 INCIDENT: {incident}")

        # Escalate to Director
        if self.shared_bus:
            self.shared_bus.publish("engine_alert", {
                "engine": payload.get("engine", "unknown"),
                "status": "critical",
                "message": incident,
            })

    # ═══════════════════════════════════════════════════════════════
    # META ENGINE → DIRECTOR
    # ═══════════════════════════════════════════════════════════════

    def _handle_alert(self, payload: Dict[str, Any]) -> None:
        """MetaEngine alert — Director should decide action."""
        engine = payload.get("engine", "")
        status = payload.get("status", "")
        message = payload.get("message", "")

        logger.warning(f"👔 DIRECTOR: Alert — {engine} is {status}")

        # Director decides what to do
        if self.director:
            try:
                self.director.enqueue_event("engine_alert", payload)
            except Exception:
                pass

    def _handle_anomaly(self, payload: Dict[str, Any]) -> None:
        """Anomaly detected — send to Director."""
        logger.warning(f"👔 DIRECTOR: Anomaly — {payload}")

        if self.director:
            try:
                self.director.enqueue_event("anomaly", payload)
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════════
    # DIRECTOR → WORKERS
    # ═══════════════════════════════════════════════════════════════

    def _handle_publish_request(self, payload: Dict[str, Any]) -> None:
        """Director requests publish — forward to Workers."""
        brand_id = payload.get("brand_id", "")
        topic = payload.get("topic", "")

        logger.info(f"📤 EXECUTION: Publish '{topic}' for {brand_id}")

        # Workers will pick this up
        if self.shared_bus:
            self.shared_bus.publish("publish_command", payload)

    def _handle_task_created(self, payload: Dict[str, Any]) -> None:
        """Task created — log for visibility."""
        logger.debug(f"📋 TASK: {payload.get('description', '')}")

    # ═══════════════════════════════════════════════════════════════
    # WORKERS → CONDITION ENGINE
    # ═══════════════════════════════════════════════════════════════

    def _handle_post_published(self, payload: Dict[str, Any]) -> None:
        """Post published — ConditionEngine should analyze."""
        topic = payload.get("topic", "")
        brand = payload.get("brand", "")

        logger.info(f"🧠 STRATEGY: Analyzing post '{topic}' for {brand}")

        if self.condition_engine:
            try:
                # Tell ConditionEngine to analyze this post
                self.condition_engine.analyze_post(payload)
            except Exception:
                pass

    def _handle_content_failed(self, payload: Dict[str, Any]) -> None:
        """Content generation failed — Director should know."""
        error = payload.get("error", "")
        brand = payload.get("brand", "")

        logger.warning(f"⚠️ CONTENT FAILED: {brand} — {error}")

        # Notify Director
        if self.shared_bus:
            self.shared_bus.publish("task_failed", {
                "description": f"Content generation failed for {brand}",
                "error": error,
            })

    def _handle_engagement(self, payload: Dict[str, Any]) -> None:
        """Engagement data received — ConditionEngine should learn."""
        logger.info(f"📊 ENGAGEMENT: {payload}")

        if self.condition_engine:
            try:
                self.condition_engine.record_engagement(payload)
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════════
    # CONDITION ENGINE → DIRECTOR
    # ═══════════════════════════════════════════════════════════════

    def _handle_strategy(self, payload: Dict[str, Any]) -> None:
        """Strategy recommendation — Director should adjust."""
        action = payload.get("action", "")
        reason = payload.get("reason", "")

        logger.info(f"🎯 STRATEGY: {action} — {reason}")

        if self.director:
            try:
                self.director.enqueue_event("strategy_update", payload)
            except Exception:
                pass

    def _handle_strategy_executed(self, payload: Dict[str, Any]) -> None:
        """Strategy executed — log result."""
        logger.info(f"✅ STRATEGY EXECUTED: {payload}")

    def _handle_brand_health(self, payload: Dict[str, Any]) -> None:
        """Brand health updated."""
        logger.debug(f"💚 BRAND HEALTH: {payload}")

    # ═══════════════════════════════════════════════════════════════
    # LEARNING LOOP
    # ═══════════════════════════════════════════════════════════════

    def _handle_task_completed(self, payload: Dict[str, Any]) -> None:
        """Task completed — learn from success."""
        description = payload.get("description", "")
        logger.info(f"✅ LEARNED: '{description}' succeeded")

        # Record learning signal
        if self.director:
            try:
                self.director.learning_loop.record_outcome(
                    type("Task", (), {
                        "capability": payload.get("capability", "unknown"),
                        "status": type("Status", (), {"COMPLETED": "COMPLETED"})("COMPLETED"),
                        "error": None,
                    }),
                    duration_ms=0,
                )
            except Exception:
                pass

    def _handle_task_failed(self, payload: Dict[str, Any]) -> None:
        """Task failed — learn from failure."""
        description = payload.get("description", "")
        error = payload.get("error", "")
        logger.warning(f"❌ LEARNED: '{description}' failed — {error}")

        # Record failure pattern
        if self.director:
            try:
                self.director.learning_loop.record_outcome(
                    type("Task", (), {
                        "capability": payload.get("capability", "unknown"),
                        "status": type("Status", (), {"FAILED": "FAILED"})("FAILED"),
                        "error": error,
                    }),
                    duration_ms=0,
                )
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════════
    # HEALTH ALERTS
    # ═══════════════════════════════════════════════════════════════

    def _publish_health_alert(self, snapshot) -> None:
        """Publish health alert if system is degraded."""
        if not self.shared_bus:
            return

        self.shared_bus.publish("engine_alert", {
            "engine": "system",
            "status": snapshot.overall_status.value,
            "message": f"System health: {snapshot.health_score}%",
            "critical_alerts": snapshot.critical_alerts,
        })

    def get_status(self) -> Dict[str, Any]:
        """Get autonomous loop status."""
        return {
            "running": self._running,
            "services_connected": self.shared_bus.get_stats() if self.shared_bus else {},
            "meta_engine": "connected" if self.meta_engine else "not connected",
            "director": "connected" if self.director else "not connected",
            "condition_engine": "connected" if self.condition_engine else "not connected",
        }


# ═══════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════

_autonomous_loop: Optional[AutonomousLoop] = None


def init_autonomous_loop(
    shared_bus=None,
    meta_engine=None,
    director=None,
    condition_engine=None,
    container=None,
) -> AutonomousLoop:
    """Initialize the autonomous loop singleton."""
    global _autonomous_loop

    _autonomous_loop = AutonomousLoop(
        shared_bus=shared_bus,
        meta_engine=meta_engine,
        director=director,
        condition_engine=condition_engine,
        container=container,
    )
    _autonomous_loop.start()
    return _autonomous_loop


def get_autonomous_loop() -> Optional[AutonomousLoop]:
    """Get the autonomous loop singleton."""
    return _autonomous_loop


__all__ = [
    "AutonomousLoop",
    "init_autonomous_loop",
    "get_autonomous_loop",
]
