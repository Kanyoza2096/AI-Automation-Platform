"""
Chaos Monkey — Deliberately breaks things to test system resilience.

Inspired by Netflix's Chaos Monkey.
RUN ONLY FOR TESTING — auto-stops after specified duration.

Usage:
  SERVICE_MODE=chaos
  CHAOS_DURATION_MINUTES=30    (optional, default 30 min)
  CHAOS_INTERVAL_SECONDS=60    (optional, default 60s between attacks)

This is the ULTIMATE test for your autonomous system:
- Runs for a limited time
- Attacks randomly
- Records results
- Auto-stops when done
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.chaos_monkey")


class ChaosMonkey:
    """Deliberately causes failures for a LIMITED TIME to test resilience."""

    # ═══════════════════════════════════════════════════════════════
    # CONFIGURATION (from env vars with defaults)
    # ═══════════════════════════════════════════════════════════════

    CHAOS_DURATION_MINUTES = int(os.getenv("CHAOS_DURATION_MINUTES", "30"))
    CHAOS_INTERVAL_SECONDS = int(os.getenv("CHAOS_INTERVAL_SECONDS", "60"))
    FLOOD_EVENT_COUNT = int(os.getenv("FLOOD_EVENT_COUNT", "50"))
    RECOVERY_CHECK_SECONDS = int(os.getenv("RECOVERY_CHECK_SECONDS", "30"))

    # ═══════════════════════════════════════════════════════════════
    # CHAOS ACTIONS
    # ═══════════════════════════════════════════════════════════════

    CHAOS_ACTIONS = [
        "kill_director",
        "kill_workers",
        "kill_meta_engine",
        "kill_condition_engine",
        "kill_trend_intelligence",
        "flood_events",
        "send_fake_alerts",
        "send_conflicting_events",
        "simulate_backup_failure",
        "simulate_content_failure",
    ]

    def __init__(self, shared_bus=None):
        self.shared_bus = shared_bus

        self._running = False
        self._thread = None
        self._chaos_count = 0
        self._attack_history: List[Dict[str, Any]] = []
        self._start_time: Optional[datetime] = None
        self._end_time: Optional[datetime] = None

    # ═══════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════

    def start(self) -> None:
        """Start chaos testing — will auto-stop after duration.

        Phase F: refused in production (see core.species_scale.assert_chaos_allowed).
        """
        if self._running:
            return

        try:
            from core.species_scale import assert_chaos_allowed
            assert_chaos_allowed()
        except Exception as exc:
            logger.error("ChaosMonkey.start blocked: %s", exc)
            return

        self._running = True
        self._start_time = datetime.now(timezone.utc)

        self._thread = threading.Thread(
            target=self._timed_chaos_loop,
            name="chaos-monkey",
            daemon=True,
        )
        self._thread.start()

        logger.warning("=" * 60)
        logger.warning("🐒 CHAOS MONKEY ACTIVATED")
        logger.warning(f"   Duration: {self.CHAOS_DURATION_MINUTES} minutes")
        logger.warning(f"   Interval: {self.CHAOS_INTERVAL_SECONDS}s between attacks")
        logger.warning(f"   Actions: {', '.join(self.CHAOS_ACTIONS[:5])}...")
        logger.warning("=" * 60)

    def stop(self) -> None:
        """Stop chaos testing."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._end_time = datetime.now(timezone.utc)
        logger.info("🐒 Chaos Monkey stopped")

    # ═══════════════════════════════════════════════════════════════
    # TIMED CHAOS LOOP — Runs for limited time, then auto-stops
    # ═══════════════════════════════════════════════════════════════

    def _timed_chaos_loop(self) -> None:
        """Run chaos for specified duration, then auto-stop."""
        start_monotonic = time.monotonic()
        duration_seconds = self.CHAOS_DURATION_MINUTES * 60

        while self._running:
            # Check if time is up
            elapsed = time.monotonic() - start_monotonic
            if elapsed >= duration_seconds:
                logger.info("=" * 60)
                logger.info("🐒 CHAOS TEST COMPLETE — Auto-stopping")
                logger.info(f"   Total attacks: {self._chaos_count}")
                logger.info(f"   Duration: {self.CHAOS_DURATION_MINUTES} minutes")
                logger.info("=" * 60)
                self._running = False
                self._end_time = datetime.now(timezone.utc)
                break

            try:
                action = random.choice(self.CHAOS_ACTIONS)
                self._execute_chaos(action)
                self._chaos_count += 1

                # Log progress
                remaining_minutes = (duration_seconds - elapsed) / 60
                logger.info(f"🐒 Attack #{self._chaos_count} done — {remaining_minutes:.0f} min remaining")

            except Exception as exc:
                logger.error(f"🐒 Chaos action failed: {exc}")

            # Wait before next attack
            time.sleep(self.CHAOS_INTERVAL_SECONDS)

    # ═══════════════════════════════════════════════════════════════
    # SINGLE ATTACK — Run one chaos action manually
    # ═══════════════════════════════════════════════════════════════

    def attack_once(self, action: Optional[str] = None) -> Dict[str, Any]:
        """Execute ONE chaos action. Useful for manual testing."""
        if action is None:
            action = random.choice(self.CHAOS_ACTIONS)

        logger.critical(f"🐒 MANUAL CHAOS: {action}")
        self._execute_chaos(action)
        self._chaos_count += 1

        return {"action": action, "completed": True, "timestamp": datetime.now(timezone.utc).isoformat()}

    def _execute_chaos(self, action: str) -> None:
        """Execute a chaos action."""
        attack_started = datetime.now(timezone.utc)

        if action == "kill_director":
            self._kill_service("director")
        elif action == "kill_workers":
            self._kill_service("workers")
        elif action == "kill_meta_engine":
            self._kill_service("meta")
        elif action == "kill_condition_engine":
            self._kill_service("condition")
        elif action == "kill_trend_intelligence":
            self._kill_trend_intelligence()
        elif action == "flood_events":
            self._flood_events()
        elif action == "send_fake_alerts":
            self._send_fake_alerts()
        elif action == "send_conflicting_events":
            self._send_conflicting_events()
        elif action == "simulate_backup_failure":
            self._simulate_backup_failure()
        elif action == "simulate_content_failure":
            self._simulate_content_failure()

        self._attack_history.append({
            "action": action,
            "timestamp": attack_started.isoformat(),
        })

    # ═══════════════════════════════════════════════════════════════
    # CHAOS ACTIONS
    # ═══════════════════════════════════════════════════════════════

    def _kill_service(self, service_name: str) -> None:
        """Simulate killing a service."""
        logger.critical(f"🐒 KILLING: {service_name}")

        if self.shared_bus:
            self.shared_bus.publish("engine_alert", {
                "engine": service_name,
                "status": "critical",
                "message": f"Chaos Monkey killed {service_name}",
            })

            self.shared_bus.publish("service_heartbeat", {
                "service": service_name,
                "status": "offline",
            })

        time.sleep(self.RECOVERY_CHECK_SECONDS)

        if self.shared_bus:
            self.shared_bus.publish("check_request", {
                "service": service_name,
            })

    def _kill_trend_intelligence(self) -> None:
        """Simulate TrendIntelligence failure."""
        logger.critical("🐒 KILLING: TrendIntelligence (no internet data)")

        if self.shared_bus:
            self.shared_bus.publish("engine_alert", {
                "engine": "trend_intelligence",
                "status": "offline",
                "message": "TrendIntelligence is down — no internet trends",
            })

        time.sleep(self.RECOVERY_CHECK_SECONDS)

        if self.shared_bus:
            self.shared_bus.publish("check_request", {
                "service": "trend_intelligence",
            })

    def _flood_events(self) -> None:
        """Flood the event bus."""
        logger.critical(f"🐒 FLOODING: {self.FLOOD_EVENT_COUNT} events")

        if self.shared_bus:
            for i in range(self.FLOOD_EVENT_COUNT):
                self.shared_bus.publish("service_heartbeat", {
                    "service": "chaos_monkey",
                    "status": "flooding",
                    "sequence": i,
                })

        logger.info("🐒 Flood complete — check rate limiting!")

    def _send_fake_alerts(self) -> None:
        """Send fake critical alerts."""
        logger.critical("🐒 SENDING: Fake critical alerts")

        fake_engines = ["fake_engine_1", "fake_engine_2", "fake_engine_3", "fake_engine_4"]

        if self.shared_bus:
            for engine in fake_engines:
                self.shared_bus.publish("engine_alert", {
                    "engine": engine,
                    "status": "critical",
                    "message": f"Fake alert from {engine}",
                })
                time.sleep(1)

    def _send_conflicting_events(self) -> None:
        """Send contradictory events."""
        logger.critical("🐒 CONFUSION: Conflicting events")

        if self.shared_bus:
            self.shared_bus.publish("service_heartbeat", {
                "service": "director",
                "status": "healthy",
            })
            time.sleep(2)

            self.shared_bus.publish("engine_alert", {
                "engine": "director",
                "status": "offline",
                "message": "Director down (contradictory)",
            })
            time.sleep(2)

            self.shared_bus.publish("service_heartbeat", {
                "service": "director",
                "status": "healthy",
            })

    def _simulate_backup_failure(self) -> None:
        """Simulate backup failures."""
        logger.critical("🐒 BACKUP FAILURE: 3 consecutive failures")

        if self.shared_bus:
            for i in range(3):
                self.shared_bus.publish("backup_completed", {
                    "status": "failed",
                    "attempt": i + 1,
                })
                time.sleep(2)

    def _simulate_content_failure(self) -> None:
        """Simulate content generation failures."""
        logger.critical("🐒 CONTENT FAILURE: 3 consecutive failures")

        if self.shared_bus:
            for i in range(3):
                self.shared_bus.publish("content_failed", {
                    "brand": "Spiritual Teacher",
                    "error": f"Simulated failure #{i + 1}",
                })
                time.sleep(2)

    # ═══════════════════════════════════════════════════════════════
    # STATUS
    # ═══════════════════════════════════════════════════════════════

    def is_complete(self) -> bool:
        """Check if chaos test is complete."""
        return self._end_time is not None

    def get_report(self) -> Dict[str, Any]:
        """Generate chaos test report."""
        return {
            "running": self._running,
            "completed": self.is_complete(),
            "chaos_count": self._chaos_count,
            "duration_minutes": self.CHAOS_DURATION_MINUTES,
            "start_time": self._start_time.isoformat() if self._start_time else None,
            "end_time": self._end_time.isoformat() if self._end_time else None,
            "attacks": self._attack_history,
            "total_attacks": len(self._attack_history),
            "actions_used": list(set(a["action"] for a in self._attack_history)),
        }

    def get_status(self) -> Dict[str, Any]:
        """Get current status (for health check)."""
        return {
            "running": self._running,
            "chaos_count": self._chaos_count,
            "start_time": self._start_time.isoformat() if self._start_time else None,
            "end_time": self._end_time.isoformat() if self._end_time else None,
            "is_complete": self.is_complete(),
        }


# ═══════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════

_chaos_monkey: Optional[ChaosMonkey] = None


def init_chaos_monkey(shared_bus=None) -> ChaosMonkey:
    """Initialize Chaos Monkey."""
    global _chaos_monkey
    _chaos_monkey = ChaosMonkey(shared_bus=shared_bus)
    _chaos_monkey.start()
    return _chaos_monkey


def get_chaos_monkey() -> Optional[ChaosMonkey]:
    return _chaos_monkey


__all__ = ["ChaosMonkey", "init_chaos_monkey", "get_chaos_monkey"]
