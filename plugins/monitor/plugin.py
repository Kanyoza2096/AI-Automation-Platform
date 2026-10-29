"""
Monitor Plugin v3.1 — Decoupled system administrator.

v3.1 CHANGES:
  - Added event bus integration for engine_alert events
  - Enhanced false positive prevention with confirmation cycles
  - Defensive event publishing with fallback handling

v3.0 CHANGES:
  - Queued alerts: no synchronous Director callbacks during checks
  - Thread pool for HTTP checks: no blocking the main loop
  - Staggered jitter: prevents collision with other engine loops
  - Bulk DB inserts: single insert for all check results
  - Alert persistence: cooldown state survives restarts (optional Redis)
  - Health check: reads from DB, doesn't trigger new checks
  - FIXED: Retry logic for bulk inserts (Resource temporarily unavailable)
"""

import logging
import random
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger("kanyoza.plugins.monitor")


# ═══════════════════════════════════════════════════════════════════════════
# EVENT BUS INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

try:
    from core.shared_event_bus import get_shared_event_bus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False
    logger.warning("Shared event bus not available — events will be skipped")


def _publish_event(event_name: str, payload: Dict[str, Any]) -> None:
    """Safely publish event to shared bus with defensive error handling."""
    if not HAS_EVENT_BUS:
        return
    try:
        bus = get_shared_event_bus()
        bus.publish(event_name, payload)
    except Exception:
        # Never let event publishing break core logic
        pass


class MonitorPlugin:
    """Watches external websites and services.

    v3.1: Event bus integration with false positive prevention.
    """

    ALERT_COOLDOWN = 300  # 5 minutes
    CHECK_INTERVAL = 60   # Base interval
    JITTER_MAX = 15       # Random jitter added to interval
    SLOW_RESPONSE_MS = 3000
    HTTP_TIMEOUT = 10
    MAX_WORKERS = 5       # Concurrent HTTP checks
    ALERT_QUEUE_DRAIN_INTERVAL = 30  # Seconds between alert queue processing
    
    # v3.1 — False positive prevention
    CONFIRMATION_CYCLES = 2  # Must be down for 2 consecutive checks before alerting
    EVENT_DEDUP_SECONDS = 300  # 5 minutes between same event

    def __init__(
        self, supabase_client=None, redis_client=None
    ):
        self.supabase = supabase_client
        self.redis = redis_client
        self.name = "monitor"
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._alert_thread: Optional[threading.Thread] = None

        # Alert state
        self._last_alerted: Dict[str, float] = {}
        self._target_states: Dict[str, str] = {}
        self._alert_queue: deque = deque(maxlen=500)
        
        # v3.1 — Event publishing state
        self._consecutive_down_cycles: Dict[str, int] = {}
        self._last_event_published: Dict[str, float] = {}

        # Stats
        self._checks_completed = 0
        self._alerts_sent = 0
        self._recovery_alerts_sent = 0

        # External callbacks (called from drain thread, not check loop)
        self._alert_callback: Optional[Callable] = None
        self._event_callback: Optional[Callable] = None

        logger.info("MonitorPlugin v3.1 initialized")

    # ═══════════════════════════════════════════════════════════════════
    # v3.0 — External callback wiring (async-safe)
    # ═══════════════════════════════════════════════════════════════════

    def set_alert_callback(self, cb: Callable) -> None:
        """Set callback for alerts. Called from drain thread, not check loop."""
        self._alert_callback = cb

    def set_event_callback(self, cb: Callable) -> None:
        """Set callback for events. Called from drain thread, not check loop."""
        self._event_callback = cb

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

        # Main check loop with jitter
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="monitor-plugin",
        )
        self._thread.start()

        # Alert drain loop (separate thread)
        self._alert_thread = threading.Thread(
            target=self._drain_alerts,
            daemon=True,
            name="monitor-alerts",
        )
        self._alert_thread.start()

        logger.info("MonitorPlugin: started (jitter=%ds, workers=%d)",
                     self.JITTER_MAX, self.MAX_WORKERS)

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
    # MAIN LOOP — with jitter
    # ═══════════════════════════════════════════════════════════════════

    def _loop(self):
        while self._running:
            try:
                self._check_all_targets()
                self._checks_completed += 1
            except Exception as e:
                logger.error("MonitorPlugin error: %s", e)

            # Sleep with jitter to prevent collision with other engines
            jitter = random.uniform(0, self.JITTER_MAX)
            sleep_time = self.CHECK_INTERVAL + jitter
            self._sleep_interruptible(sleep_time)

    def _sleep_interruptible(self, seconds: float):
        """Sleep in small chunks so stop() is responsive."""
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            time.sleep(min(1.0, end - time.monotonic()))

    # ═══════════════════════════════════════════════════════════════════
    # CHECK TARGETS — concurrent HTTP
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

        # Concurrent HTTP checks
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

        # Bulk insert all results with retry
        if results:
            self._bulk_insert_results_with_retry(results)

    def _check_single_target(self, target: dict) -> Optional[dict]:
        """Check a single target. Returns result dict or None."""
        url = target["url"]
        target_id = target["id"]
        target_name = target.get("name", url)
        expected_status = target.get("expected_status", 200)

        start = time.monotonic()
        previous_state = self._target_states.get(target_id, "unknown")

        try:
            # SSRF guard — block private/metadata/non-http URLs (report §30)
            from core.ssrf_guard import validate_public_http_url
            ok, reason = validate_public_http_url(url, resolve_dns=True)
            if not ok:
                logger.warning("MonitorPlugin: SSRF blocked url=%s reason=%s", url[:120], reason)
                elapsed_ms = int((time.monotonic() - start) * 1000)
                self._target_states[target_id] = "blocked"
                return {
                    "target_id": target_id,
                    "status": "blocked",
                    "http_status": None,
                    "latency_ms": elapsed_ms,
                    "error": f"ssrf_blocked:{reason}",
                    "url": url,
                }

            resp = requests.get(
                url,
                timeout=self.HTTP_TIMEOUT,
                allow_redirects=False,  # reduce open-redirect SSRF risk
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            status = "up" if resp.status_code == expected_status else "down"

            # Queue alert if down
            if status == "down":
                # Track consecutive down cycles
                self._consecutive_down_cycles[target_id] = self._consecutive_down_cycles.get(target_id, 0) + 1
                
                # Only alert after confirmation cycles
                if self._should_alert(target_id):
                    self._queue_alert(
                        target, f"HTTP {resp.status_code} (expected {expected_status})"
                    )
                    self._publish_engine_alert(target, "offline", f"HTTP {resp.status_code} (expected {expected_status})")
            else:
                # Reset consecutive down counter on recovery
                self._consecutive_down_cycles[target_id] = 0
                
                # Recovery notification
                if previous_state == "down":
                    self._queue_recovery_alert(target, elapsed_ms)
                    self._publish_engine_alert(target, "recovered", f"Recovered in {elapsed_ms}ms")

                # Slow response warning
                if elapsed_ms > self.SLOW_RESPONSE_MS:
                    self._queue_slow_alert(target, elapsed_ms)

                # Clear alert cooldown on recovery
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
            
            # Track consecutive down cycles
            self._consecutive_down_cycles[target_id] = self._consecutive_down_cycles.get(target_id, 0) + 1
            
            # Only alert after confirmation cycles
            if self._should_alert(target_id):
                self._queue_alert(target, str(e)[:200])
                self._publish_engine_alert(target, "offline", str(e)[:200])
            
            self._target_states[target_id] = "down"

            return {
                "target_id": target_id,
                "status": "down",
                "http_status": None,
                "response_time_ms": elapsed_ms,
                "error_message": str(e)[:500],
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

    def _should_alert(self, target_id: str) -> bool:
        """Determine if alert should be sent based on confirmation cycles."""
        consecutive = self._consecutive_down_cycles.get(target_id, 0)
        return consecutive >= self.CONFIRMATION_CYCLES

    def _publish_engine_alert(self, target: dict, status: str, message: str) -> None:
        """Publish engine_alert event with deduplication."""
        target_id = target["id"]
        now = time.monotonic()
        
        # Check dedup window
        if now - self._last_event_published.get(target_id, 0) < self.EVENT_DEDUP_SECONDS:
            return
        
        self._last_event_published[target_id] = now
        
        # Publish event
        _publish_event("engine_alert", {
            "engine": target.get("name", target["url"]),
            "status": status,
            "message": f"Monitor detected {target.get('name', target['url'])} is {status}: {message}",
            "url": target["url"],
            "target_id": target_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ═══════════════════════════════════════════════════════════════════
    # v3.0 — BULK INSERT WITH RETRY
    # ═══════════════════════════════════════════════════════════════════

    def _bulk_insert_results_with_retry(self, results: List[dict], max_retries: int = 3):
        """Insert all check results with exponential backoff retry."""
        for attempt in range(max_retries):
            try:
                self.supabase.table("monitor_checks").insert(results).execute()
                return  # Success — exit
            except Exception as e:
                # Check if it's a connection/availability error
                error_msg = str(e).lower()
                is_retryable = (
                    "resource temporarily unavailable" in error_msg
                    or "connection" in error_msg
                    or "timeout" in error_msg
                    or "pool" in error_msg
                )

                if not is_retryable or attempt == max_retries - 1:
                    # Surface PostgREST/API error body (often the 400 reason)
                    detail = getattr(e, "message", None) or getattr(e, "args", None) or e
                    logger.error("MonitorPlugin: bulk insert failed: %s", detail)
                    self._bulk_insert_results_individual(results)
                    return

                # Exponential backoff
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                logger.warning(
                    "MonitorPlugin: bulk insert retry %d/%d in %.2fs: %s",
                    attempt + 1, max_retries, wait_time, e
                )
                time.sleep(wait_time)

    def _bulk_insert_results_individual(self, results: List[dict]):
        """Fallback: insert results one by one."""
        for result in results:
            try:
                self.supabase.table("monitor_checks").insert(result).execute()
            except Exception as inner_e:
                logger.error(
                    "MonitorPlugin: individual insert failed target_id=%s keys=%s err=%s",
                    result.get("target_id"),
                    list(result.keys()),
                    inner_e,
                )

    # ═══════════════════════════════════════════════════════════════════
    # v3.0 — QUEUED ALERTS (no synchronous callbacks)
    # ═══════════════════════════════════════════════════════════════════

    def _queue_alert(self, target: dict, message: str):
        """Queue an alert for async processing."""
        target_id = target["id"]
        now_ts = time.monotonic()
        last = self._last_alerted.get(target_id, 0)

        if now_ts - last < self.ALERT_COOLDOWN:
            return

        self._last_alerted[target_id] = now_ts
        self._alert_queue.append({
            "type": "down",
            "target_id": target_id,
            "target_name": target.get("name", target["url"]),
            "url": target["url"],
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _queue_recovery_alert(self, target: dict, elapsed_ms: int):
        """Queue a recovery alert."""
        self._alert_queue.append({
            "type": "recovery",
            "target_id": target["id"],
            "target_name": target.get("name", target["url"]),
            "url": target["url"],
            "elapsed_ms": elapsed_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _queue_slow_alert(self, target: dict, elapsed_ms: int):
        """Queue a slow response alert with longer cooldown."""
        target_id = target["id"]
        slow_key = f"slow_{target_id}"
        now_ts = time.monotonic()
        last = self._last_alerted.get(slow_key, 0)

        if now_ts - last < 900:  # 15 min cooldown
            return

        self._last_alerted[slow_key] = now_ts
        self._alert_queue.append({
            "type": "slow",
            "target_id": target_id,
            "target_name": target.get("name", target["url"]),
            "url": target["url"],
            "elapsed_ms": elapsed_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ═══════════════════════════════════════════════════════════════════
    # v3.0 — ASYNC ALERT DRAIN (separate thread)
    # ═══════════════════════════════════════════════════════════════════

    def _drain_alerts(self):
        """Process alert queue in a separate thread."""
        while self._running:
            self._process_alert_batch()
            time.sleep(self.ALERT_QUEUE_DRAIN_INTERVAL)

        # Drain remaining on shutdown
        self._process_alert_batch()

    def _process_alert_batch(self):
        """Process all queued alerts."""
        while self._alert_queue:
            try:
                alert = self._alert_queue.popleft()
                self._process_single_alert(alert)
            except Exception as e:
                logger.error("MonitorPlugin: alert processing failed: %s", e)

    def _process_single_alert(self, alert: dict):
        """Process a single alert — Telegram + event callback."""
        alert_type = alert["type"]

        if alert_type == "down":
            self._send_telegram_alert(alert)
            self._alerts_sent += 1
        elif alert_type == "recovery":
            self._send_telegram_recovery(alert)
            self._recovery_alerts_sent += 1
        elif alert_type == "slow":
            self._send_telegram_slow(alert)

        # Fire event callback (safe — we're in drain thread, not check loop)
        if self._event_callback:
            try:
                self._event_callback("monitor_alert", alert)
            except Exception as e:
                logger.debug("MonitorPlugin: event callback failed: %s", e)

    def _send_telegram_alert(self, alert: dict):
        """Send down alert via Telegram."""
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
        """Send recovery alert via Telegram."""
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
        """Send slow response alert via Telegram."""
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
        """Return monitoring statistics."""
        return {
            "checks_completed": self._checks_completed,
            "alerts_sent": self._alerts_sent,
            "recovery_alerts_sent": self._recovery_alerts_sent,
            "targets_monitored": len(self._target_states),
            "targets_down": sum(
                1 for s in self._target_states.values() if s == "down"
            ),
            "alert_queue_depth": len(self._alert_queue),
            "running": self._running,
        }

    def health_check(self) -> dict:
        """Quick health check — no DB queries."""
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

    def _handle_status(self, params: dict) -> dict:
        if not self.supabase:
            return {"ok": False, "error": "Database not available"}

        try:
            targets = (
                self.supabase.table("monitor_targets")
                .select("*")
                .execute()
            )
        except Exception as e:
            return {"ok": False, "error": str(e)}

        return {
            "ok": True,
            "targets": targets.data or [],
            "stats": self.get_stats(),
        }

    def _handle_check_all(self, params: dict) -> dict:
        self._check_all_targets()
        return {"ok": True, "message": "All checks triggered"}


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
