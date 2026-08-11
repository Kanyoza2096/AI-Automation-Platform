"""
Monitor Plugin v2.0 — Autonomous system administrator.
Checks websites and APIs, alerts on failure via Telegram.

v2.0 UPGRADES:
  - Recovery notifications (alert when site comes back up)
  - Uptime statistics & health check
  - Proper Director event callback integration
  - Configurable check intervals
  - Response time tracking with thresholds
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests

logger = logging.getLogger("kanyoza.plugins.monitor")


class MonitorPlugin:
    """Watches external websites and services.

    v2.0: Recovery alerts, uptime stats, proper Director integration.
    """

    # Alert cooldown in seconds
    ALERT_COOLDOWN = 300  # 5 minutes
    # Default check interval
    CHECK_INTERVAL = 60  # 1 minute
    # Response time warning threshold (ms)
    SLOW_RESPONSE_MS = 3000

    def __init__(
        self, supabase_client=None, redis_client=None
    ):
        self.supabase = supabase_client
        self.redis = redis_client
        self.name = "monitor"
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_alerted: Dict[str, float] = {}
        self._target_states: Dict[str, str] = (
            {}
        )  # track up/down state changes
        self._checks_completed = 0
        self._alerts_sent = 0
        self._recovery_alerts_sent = 0

    # ═══════════════════════════════════════════════════════════════════
    # PLUGIN INTERFACE
    # ═══════════════════════════════════════════════════════════════════

    def get_commands(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "monitor_status",
                "description": "Get monitoring status",
                "handler": self._handle_status,
            },
            {
                "name": "monitor_check_all",
                "description": "Run all checks now",
                "handler": self._handle_check_all,
            },
        ]

    def get_webhooks(self) -> List[Dict[str, Any]]:
        return []

    # ═══════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="monitor-plugin",
        )
        self._thread.start()
        logger.info("MonitorPlugin: started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("MonitorPlugin: stopped")

    def shutdown(self):
        """Alternative stop for ShutdownRegistry compatibility."""
        self.stop()

    # ═══════════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════════════════

    def _loop(self):
        while self._running:
            try:
                self._check_all_targets()
                self._checks_completed += 1
            except Exception as e:
                logger.error(
                    "MonitorPlugin error: %s", e
                )
            time.sleep(self.CHECK_INTERVAL)

    def _check_all_targets(self):
        if not self.supabase:
            return

        targets = (
            self.supabase.table("monitor_targets")
            .select("*")
            .eq("is_paused", False)
            .execute()
        )

        for target in targets.data or []:
            self._check_target(target)

    def _check_target(self, target: dict):
        url = target["url"]
        target_id = target["id"]
        target_name = target.get("name", url)
        expected_status = target.get(
            "expected_status", 200
        )

        start = time.monotonic()
        previous_state = self._target_states.get(
            target_id, "unknown"
        )

        try:
            resp = requests.get(
                url,
                timeout=10,
                allow_redirects=True,
            )
            elapsed_ms = int(
                (time.monotonic() - start) * 1000
            )
            status = (
                "up"
                if resp.status_code == expected_status
                else "down"
            )

            # Store check result
            self.supabase.table(
                "monitor_checks"
            ).insert({
                "target_id": target_id,
                "status": status,
                "http_status": resp.status_code,
                "response_time_ms": elapsed_ms,
                "checked_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }).execute()

            if status == "down":
                self._send_alert(
                    target,
                    f"HTTP {resp.status_code} "
                    f"(expected {expected_status})",
                )
            else:
                # v2.0 — Recovery notification
                if previous_state == "down":
                    self._send_recovery_alert(
                        target, elapsed_ms
                    )

                # v2.0 — Slow response warning
                if elapsed_ms > self.SLOW_RESPONSE_MS:
                    self._send_slow_response_alert(
                        target, elapsed_ms
                    )

                # Clear alert cooldown on recovery
                self._last_alerted.pop(target_id, None)

            self._target_states[target_id] = status

        except Exception as e:
            elapsed_ms = int(
                (time.monotonic() - start) * 1000
            )

            self.supabase.table(
                "monitor_checks"
            ).insert({
                "target_id": target_id,
                "status": "down",
                "response_time_ms": elapsed_ms,
                "error_message": str(e)[:500],
                "checked_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }).execute()

            self._send_alert(
                target, str(e)[:200]
            )
            self._target_states[target_id] = "down"

    # ═══════════════════════════════════════════════════════════════════
    # v2.0 — ALERTS
    # ═══════════════════════════════════════════════════════════════════

    def _send_alert(
        self, target: dict, message: str
    ):
        """Send down alert via Telegram with cooldown."""
        target_id = target["id"]
        target_name = target.get("name", target["url"])

        logger.warning(
            "MonitorPlugin: %s is DOWN — %s",
            target_name,
            message,
        )

        # Cooldown check
        now_ts = time.monotonic()
        last = self._last_alerted.get(target_id, 0)
        if now_ts - last < self.ALERT_COOLDOWN:
            return
        self._last_alerted[target_id] = now_ts
        self._alerts_sent += 1

        # Telegram alert
        try:
            from services.email_service import (
                email_service,
            )

            email_service.send_telegram(
                f"🔴 <b>SITE DOWN: {target_name}</b>\n"
                f"URL: {target['url']}\n"
                f"Error: {message}\n"
                f"<i>{datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>"
            )
        except Exception as exc:
            logger.error(
                "MonitorPlugin: Telegram alert failed: %s",
                exc,
            )

        # v2.0 — Use Director's event callback properly
        try:
            from services.director import director

            if (
                director
                and hasattr(director, "event_cb")
                and director.event_cb
            ):
                director.event_cb(
                    "monitor_alert",
                    {
                        "target": target_name,
                        "url": target["url"],
                        "error": message,
                        "timestamp": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    },
                )
        except Exception:
            pass

    def _send_recovery_alert(
        self, target: dict, elapsed_ms: int
    ):
        """v2.0 — Send alert when a site recovers."""
        target_name = target.get("name", target["url"])
        logger.info(
            "MonitorPlugin: %s RECOVERED (%dms)",
            target_name,
            elapsed_ms,
        )
        self._recovery_alerts_sent += 1

        try:
            from services.email_service import (
                email_service,
            )

            email_service.send_telegram(
                f"🟢 <b>SITE RECOVERED: {target_name}</b>\n"
                f"URL: {target['url']}\n"
                f"Response time: {elapsed_ms}ms\n"
                f"<i>{datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>"
            )
        except Exception as exc:
            logger.debug(
                "Recovery alert failed: %s", exc
            )

    def _send_slow_response_alert(
        self, target: dict, elapsed_ms: int
    ):
        """v2.0 — Warn when a site is slow but still up."""
        target_name = target.get("name", target["url"])

        # Longer cooldown for slow response alerts
        target_id = target["id"]
        now_ts = time.monotonic()
        slow_key = f"slow_{target_id}"
        last = self._last_alerted.get(slow_key, 0)
        if now_ts - last < 900:  # 15 min cooldown
            return
        self._last_alerted[slow_key] = now_ts

        logger.warning(
            "MonitorPlugin: %s is SLOW — %dms",
            target_name,
            elapsed_ms,
        )

        try:
            from services.email_service import (
                email_service,
            )

            email_service.send_telegram(
                f"🟡 <b>SLOW RESPONSE: {target_name}</b>\n"
                f"URL: {target['url']}\n"
                f"Response time: {elapsed_ms}ms "
                f"(threshold: {self.SLOW_RESPONSE_MS}ms)\n"
                f"<i>{datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>"
            )
        except Exception as exc:
            logger.debug(
                "Slow response alert failed: %s", exc
            )

    # ═══════════════════════════════════════════════════════════════════
    # STATISTICS
    # ═══════════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        """Return monitoring statistics."""
        return {
            "checks_completed": self._checks_completed,
            "alerts_sent": self._alerts_sent,
            "recovery_alerts_sent": (
                self._recovery_alerts_sent
            ),
            "targets_monitored": len(
                self._target_states
            ),
            "targets_down": sum(
                1
                for s in self._target_states.values()
                if s == "down"
            ),
            "running": self._running,
        }

    def health_check(self) -> dict:
        """Quick health check."""
        stats = self.get_stats()
        status = "healthy"

        if not self._running:
            status = "stopped"
        elif stats["targets_down"] > 0:
            status = "degraded"

        return {
            "status": status,
            "stats": stats,
        }

    # ═══════════════════════════════════════════════════════════════════
    # COMMAND HANDLERS
    # ═══════════════════════════════════════════════════════════════════

    def _handle_status(
        self, params: dict
    ) -> dict:
        if not self.supabase:
            return {
                "ok": False,
                "error": "Database not available",
            }
        targets = (
            self.supabase.table("monitor_targets")
            .select("*")
            .execute()
        )
        stats = self.get_stats()
        return {
            "ok": True,
            "targets": targets.data or [],
            "stats": stats,
        }

    def _handle_check_all(
        self, params: dict
    ) -> dict:
        self._check_all_targets()
        return {
            "ok": True,
            "message": "All checks triggered",
        }


# ═══════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

monitor_plugin: Optional[MonitorPlugin] = None


def init_monitor_plugin(
    supabase_client=None, redis_client=None
) -> MonitorPlugin:
    global monitor_plugin
    if monitor_plugin is None:
        monitor_plugin = MonitorPlugin(
            supabase_client, redis_client
        )
        monitor_plugin.start()
    return monitor_plugin
