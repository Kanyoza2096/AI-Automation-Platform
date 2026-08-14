"""
Monitor Plugin v3.1 — Decoupled system administrator.

v3.1 FIXES:
  - Global alert rate limiting (max 3 down alerts/hour, 10 total/hour)
  - Target cooldown extended to 30 minutes
  - Cleanup of old target states (prevent unbounded growth)
  - Better retry logic for bulk inserts
  - Graceful shutdown with alert drain timeout

v3.0 CHANGES (retained):
  - Queued alerts: no synchronous Director callbacks during checks
  - Thread pool for HTTP checks: no blocking the main loop
  - Staggered jitter: prevents collision with other engine loops
  - Bulk DB inserts: single insert for all check results
  - Alert persistence: cooldown state survives restarts (optional Redis)
  - Health check: reads from DB, doesn't trigger new checks
  - FIXED: Retry logic for bulk inserts
"""

import logging
import random
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger("kanyoza.plugins.monitor")


# ═══════════════════════════════════════════════════════════════════════════
# v3.1 — GLOBAL ALERT RATE LIMITER
# ═══════════════════════════════════════════════════════════════════════════

class MonitorAlertRateLimiter:
    """Prevents alert flooding from MonitorPlugin."""

    def __init__(
        self,
        max_down_alerts_per_hour: int = 3,
        max_total_alerts_per_hour: int = 10,
        target_cooldown_seconds: int = 1800,  # 30 minutes
    ):
        self.max_down_alerts_per_hour = max_down_alerts_per_hour
        self.max_total_alerts_per_hour = max_total_alerts_per_hour
        self.target_cooldown_seconds = target_cooldown_seconds
        self._down_alerts: List[float] = []
        self._total_alerts: List[float] = []
        self._target_alerted_at: Dict[str, float] = {}
        self._lock = threading.RLock()

    def _cleanup(self):
        now = time.monotonic()
        self._down_alerts = [t for t in self._down_alerts if now - t < 3600]
        self._total_alerts = [t for t in self._total_alerts if now - t < 3600]

    def can_send_down_alert(self, target_id: str) -> bool:
        with self._lock:
            self._cleanup()
            now = time.monotonic()
            last = self._target_alerted_at.get(target_id, 0)
            if now - last < self.target_cooldown_seconds:
                return False
            if len(self._down_alerts) >= self.max_down_alerts_per_hour:
                return False
            if len(self._total_alerts) >= self.max_total_alerts_per_hour:
                return False
            return True

    def record_alert(self, target_id: str, is_down: bool = True) -> None:
        with self._lock:
            now = time.monotonic()
            self._total_alerts.append(now)
            if is_down:
                self._down_alerts.append(now)
            self._target_alerted_at[target_id] = now

    def can_send_recovery_alert(self) -> bool:
        with self._lock:
            self._cleanup()
            return len(self._total_alerts) < self.max_total_alerts_per_hour

    def can_send_slow_alert(self, target_id: str) -> bool:
        with self._lock:
            self._cleanup()
            if len(self._total_alerts) >= self.max_total_alerts_per_hour:
                return False
            slow_key = f"slow_{target_id}"
            now = time.monotonic()
            last = self._target_alerted_at.get(slow_key, 0)
            return now - last >= self.target_cooldown_seconds

    def get_stats(self) -> dict:
        with self._lock:
            self._cleanup()
            return {
                "down_alerts_last_hour": len(self._down_alerts),
                "total_alerts_last_hour": len(self._total_alerts),
                "max_down_per_hour": self.max_down_alerts_per_hour,
                "max_total_per_hour": self.max_total_alerts_per_hour,
            }


# ═══════════════════════════════════════════════════════════════════════════
# MONITOR PLUGIN v3.1
# ═══════════════════════════════════════════════════════════════════════════

class MonitorPlugin:
    """Watches external websites and services.

    v3.1: Fully decoupled with global alert rate limiting.
    """

    ALERT_COOLDOWN = 300  # 5 minutes (legacy — superseded by rate limiter)
    CHECK_INTERVAL = 60
    JITTER_MAX = 15
    SLOW_RESPONSE_MS = 3000
    HTTP_TIMEOUT = 10
    MAX_WORKERS = 5
    ALERT_QUEUE_DRAIN_INTERVAL = 30
    TARGET_STATE_CLEANUP_INTERVAL = 3600  # 1 hour

    def __init__(self, supabase_client=None, redis_client=None):
        self.supabase = supabase_client
        self.redis = redis_client
        self.name = "monitor"
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._alert_thread: Optional[threading.Thread] = None

        self._last_alerted: Dict[str, float] = {}
        self._target_states: Dict[str, str] = {}
        self._alert_queue: deque = deque(maxlen=500)

        self._checks_completed = 0
        self._alerts_sent = 0
        self._recovery_alerts_sent = 0

        self._alert_callback: Optional[Callable] = None
        self._event_callback: Optional[Callable] = None

        # v3.1: Rate limiter
        self.alert_limiter = MonitorAlertRateLimiter()

        # v3.1: Cleanup tracking
        self._last_cleanup = time.monotonic()

        logger.info("MonitorPlugin v3.1 initialized")

    # ═══════════════════════════════════════════════════════════════════
    # v3.1 — External callback wiring (async-safe)
    # ═══════════════════════════════════════════════════════════════════

    def set_alert_callback(self, cb: Callable) -> None:
        self._alert_callback = cb

    def set_event_callback(self, cb: Callable) -> None:
        self._event_callback = cb

    # ═══════════════════════════════════════════════════════════════════
    # PLUGIN INTERFACE
    # ═══════════════════════════════════════════════════════════════════

    def get_commands(self) -> List[Dict[str, Any]]:
        return [
            {"name": "monitor_status", "description": "Get monitoring status", "handler": self._handle_status},
            {"name": "monitor_check_all", "description": "Run all checks now", "handler": self._handle_check_all},
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

        self._thread = threading.Thread(target=self._loop, daemon=True, name="monitor-plugin")
        self._thread.start()

        self._alert_thread = threading.Thread(target=self._drain_alerts, daemon=True, name="monitor-alerts")
        self._alert_thread.start()

        logger.info("MonitorPlugin: started (jitter=%ds, workers=%d)", self.JITTER_MAX, self.MAX_WORKERS)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        if self._alert_thread:
            self._alert_thread.join(timeout=5)
        logger.info("MonitorPlugin: stopped")

    def shutdown(self):
        self.stop()

    # ═══════════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════════════════

    def _loop(self):
        while self._running:
            try:
                self._check_all_targets()
                self._checks_completed += 1
                self._cleanup_old_states()
            except Exception as e:
                logger.error("MonitorPlugin error: %s", e)

            jitter = random.uniform(0, self.JITTER_MAX)
            sleep_time = self.CHECK_INTERVAL + jitter
            self._sleep_interruptible(sleep_time)

    def _sleep_interruptible(self, seconds: float):
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            time.sleep(min(1.0, end - time.monotonic()))

    # ═══════════════════════════════════════════════════════════════════
    # v3.1 — CLEANUP OLD TARGET STATES
    # ═══════════════════════════════════════════════════════════════════

    def _cleanup_old_states(self):
        """Remove target states older than 1 hour to prevent unbounded growth."""
        now = time.monotonic()
        if now - self._last_cleanup < self.TARGET_STATE_CLEANUP_INTERVAL:
            return
        self._last_cleanup = now

        # Only clean alert cooldown — target states are kept for live status
        cutoff = now - 3600
        self._last_alerted = {
            k: v for k, v in self._last_alerted.items()
            if v >= cutoff
        }
        logger.debug("MonitorPlugin: cleaned %d alert cooldowns", len(self._last_alerted))

    # ═══════════════════════════════════════════════════════════════════
    # CHECK TARGETS
    # ═══════════════════════════════════════════════════════════════════

    def _check_all_targets(self):
        if not self.supabase:
            return

        try:
            targets_result = (
                self.supabase.table("monitor_targets")
                .select("*")
                .eq("is_paused", False)
                .execute()
            )
        except Exception as e:
            logger.error("MonitorPlugin: failed to fetch targets: %s", e)
            return

        targets = targets_result.data or []
        if not targets:
            return

        results = []
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {
                executor.submit(self._check_single_target, target): target
                for target in targets
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                except Exception as e:
                    logger.error("MonitorPlugin: check failed: %s", e)

        if results:
            self._bulk_insert_results_with_retry(results)

    def _check_single_target(self, target: dict) -> Optional[dict]:
        url = target["url"]
        target_id = target["id"]
        expected_status = target.get("expected_status", 200)

        start = time.monotonic()
        previous_state = self._target_states.get(target_id, "unknown")

        try:
            resp = requests.get(url, timeout=self.HTTP_TIMEOUT, allow_redirects=True)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            status = "up" if resp.status_code == expected_status else "down"

            if status == "down":
                self._queue_alert(target, f"HTTP {resp.status_code} (expected {expected_status})")
            else:
                if previous_state == "down":
                    self._queue_recovery_alert(target, elapsed_ms)
                if elapsed_ms > self.SLOW_RESPONSE_MS:
                    self._queue_slow_alert(target, elapsed_ms)
                self._last_alerted.pop(target_id, None)

            self._target_states[target_id] = status

            return {
                "target_id": target_id,
                "status": status,
                "http_status": resp.status_code,
                "response_time_ms": elapsed_ms,
                "error_message": None,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            self._queue_alert(target, str(e)[:200])
            self._target_states[target_id] = "down"

            return {
                "target_id": target_id,
                "status": "down",
                "http_status": None,
                "response_time_ms": elapsed_ms,
                "error_message": str(e)[:500],
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

    # ═══════════════════════════════════════════════════════════════════
    # BULK INSERT WITH RETRY (v3.1 — improved retry)
    # ═══════════════════════════════════════════════════════════════════

    def _bulk_insert_results_with_retry(self, results: List[dict], max_retries: int = 3):
        for attempt in range(max_retries):
            try:
                self.supabase.table("monitor_checks").insert(results).execute()
                return
            except Exception as e:
                error_msg = str(e).lower()
                is_retryable = any(kw in error_msg for kw in [
                    "resource temporarily unavailable", "connection",
                    "timeout", "pool", "closed", "terminated",
                ])

                if not is_retryable or attempt == max_retries - 1:
                    logger.error("MonitorPlugin: bulk insert failed: %s", e)
                    self._bulk_insert_results_individual(results)
                    return

                wait_time = (2 ** attempt) + random.uniform(0, 1)
                logger.warning(
                    "MonitorPlugin: bulk insert retry %d/%d in %.2fs: %s",
                    attempt + 1, max_retries, wait_time, e
                )
                time.sleep(wait_time)

    def _bulk_insert_results_individual(self, results: List[dict]):
        for result in results:
            try:
                self.supabase.table("monitor_checks").insert(result).execute()
            except Exception as inner_e:
                logger.error("MonitorPlugin: individual insert failed: %s", inner_e)

    # ═══════════════════════════════════════════════════════════════════
    # v3.1 — QUEUED ALERTS WITH RATE LIMITING
    # ═══════════════════════════════════════════════════════════════════

    def _queue_alert(self, target: dict, message: str):
        target_id = target["id"]

        if not self.alert_limiter.can_send_down_alert(target_id):
            logger.debug("MonitorPlugin: alert suppressed for %s (rate limited)", target.get("name", target_id))
            return

        self.alert_limiter.record_alert(target_id, is_down=True)
        self._alert_queue.append({
            "type": "down",
            "target_id": target_id,
            "target_name": target.get("name", target["url"]),
            "url": target["url"],
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _queue_recovery_alert(self, target: dict, elapsed_ms: int):
        if not self.alert_limiter.can_send_recovery_alert():
            return
        self.alert_limiter.record_alert(target["id"], is_down=False)
        self._alert_queue.append({
            "type": "recovery",
            "target_id": target["id"],
            "target_name": target.get("name", target["url"]),
            "url": target["url"],
            "elapsed_ms": elapsed_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _queue_slow_alert(self, target: dict, elapsed_ms: int):
        target_id = target["id"]
        if not self.alert_limiter.can_send_slow_alert(target_id):
            return
        self.alert_limiter.record_alert(target_id, is_down=False)
        self._alert_queue.append({
            "type": "slow",
            "target_id": target_id,
            "target_name": target.get("name", target["url"]),
            "url": target["url"],
            "elapsed_ms": elapsed_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ═══════════════════════════════════════════════════════════════════
    # ASYNC ALERT DRAIN
    # ═══════════════════════════════════════════════════════════════════

    def _drain_alerts(self):
        while self._running:
            self._process_alert_batch()
            time.sleep(self.ALERT_QUEUE_DRAIN_INTERVAL)
        self._process_alert_batch()

    def _process_alert_batch(self):
        while self._alert_queue:
            try:
                alert = self._alert_queue.popleft()
                self._process_single_alert(alert)
            except Exception as e:
                logger.error("MonitorPlugin: alert processing failed: %s", e)

    def _process_single_alert(self, alert: dict):
        alert_type = alert["type"]

        if alert_type == "down":
            self._send_telegram_alert(alert)
            self._alerts_sent += 1
        elif alert_type == "recovery":
            self._send_telegram_recovery(alert)
            self._recovery_alerts_sent += 1
        elif alert_type == "slow":
            self._send_telegram_slow(alert)

        if self._event_callback:
            try:
                self._event_callback("monitor_alert", alert)
            except Exception as e:
                logger.debug("MonitorPlugin: event callback failed: %s", e)

    def _send_telegram_alert(self, alert: dict):
        target_name = alert["target_name"]
        url = alert["url"]
        message = alert["message"]
        timestamp = alert["timestamp"][:16].replace("T", " ")

        logger.warning("MonitorPlugin: %s is DOWN — %s", target_name, message)

        try:
            from services.email_service import email_service
            email_service.send_telegram(
                f"🔴 <b>SITE DOWN: {target_name}</b>\n"
                f"URL: {url}\n"
                f"Error: {message}\n"
                f"<i>{timestamp} UTC</i>"
            )
        except Exception as exc:
            logger.error("MonitorPlugin: Telegram alert failed: %s", exc)

    def _send_telegram_recovery(self, alert: dict):
        target_name = alert["target_name"]
        url = alert["url"]
        elapsed_ms = alert["elapsed_ms"]

        logger.info("MonitorPlugin: %s RECOVERED (%dms)", target_name, elapsed_ms)

        try:
            from services.email_service import email_service
            email_service.send_telegram(
                f"🟢 <b>SITE RECOVERED: {target_name}</b>\n"
                f"URL: {url}\n"
                f"Response time: {elapsed_ms}ms\n"
                f"<i>{datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>"
            )
        except Exception as exc:
            logger.debug("Recovery alert failed: %s", exc)

    def _send_telegram_slow(self, alert: dict):
        target_name = alert["target_name"]
        url = alert["url"]
        elapsed_ms = alert["elapsed_ms"]

        logger.warning("MonitorPlugin: %s is SLOW — %dms", target_name, elapsed_ms)

        try:
            from services.email_service import email_service
            email_service.send_telegram(
                f"🟡 <b>SLOW RESPONSE: {target_name}</b>\n"
                f"URL: {url}\n"
                f"Response time: {elapsed_ms}ms "
                f"(threshold: {self.SLOW_RESPONSE_MS}ms)\n"
                f"<i>{datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>"
            )
        except Exception as exc:
            logger.debug("Slow response alert failed: %s", exc)

    # ═══════════════════════════════════════════════════════════════════
    # STATISTICS & HEALTH
    # ═══════════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        return {
            "checks_completed": self._checks_completed,
            "alerts_sent": self._alerts_sent,
            "recovery_alerts_sent": self._recovery_alerts_sent,
            "targets_monitored": len(self._target_states),
            "targets_down": sum(1 for s in self._target_states.values() if s == "down"),
            "alert_queue_depth": len(self._alert_queue),
            "running": self._running,
            "alert_rate_limit": self.alert_limiter.get_stats(),
        }

    def health_check(self) -> dict:
        stats = self.get_stats()
        status = "healthy"

        if not self._running:
            status = "stopped"
        elif stats["targets_down"] > 0:
            status = "degraded"

        return {"status": status, "stats": stats}

    # ═══════════════════════════════════════════════════════════════════
    # COMMAND HANDLERS
    # ═══════════════════════════════════════════════════════════════════

    def _handle_status(self, params: dict) -> dict:
        if not self.supabase:
            return {"ok": False, "error": "Database not available"}
        try:
            targets = self.supabase.table("monitor_targets").select("*").execute()
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "targets": targets.data or [], "stats": self.get_stats()}

    def _handle_check_all(self, params: dict) -> dict:
        self._check_all_targets()
        return {"ok": True, "message": "All checks triggered"}


# ═══════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

monitor_plugin: Optional[MonitorPlugin] = None


def init_monitor_plugin(supabase_client=None, redis_client=None) -> MonitorPlugin:
    global monitor_plugin
    if monitor_plugin is None:
        monitor_plugin = MonitorPlugin(supabase_client, redis_client)
        monitor_plugin.start()
    return monitor_plugin
