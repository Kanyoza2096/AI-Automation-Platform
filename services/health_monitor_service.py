"""
Health Monitor Service v4.0 — Async-Native Autonomous System Health Watchdog.

v4.0 CHANGES (hardening, backward compatible):
  - Alert delivery is queued + rate-limited; no unbounded task creation.
  - Three-state health model: ok / unknown / down. Alerts fire only on
    ok <-> down transitions. Probe timeouts no longer emit false recovery.
  - _check_gemini is async; API key sent in header, never in URL.
  - _check_redis and _check_supabase have explicit timeouts.
  - Event-driven sleep (no 30x1s chunk loops).
  - Daily summary dedup persisted to Redis when available.
  - Structured logs + optional metrics hooks.
  - health_check() reports "unknown" until the first cycle completes.
  - Registry snapshot integration: if lifecycle.ServiceHealthRegistry exposes
    read_snapshot()/publish_snapshot() (v-next), this uses it; otherwise it
    falls back to check_all() exactly as before.

v3.0 (unchanged behavior preserved):
  - Fully async-native (no threading.Thread, no time.sleep).
  - UnifiedEventBus integration.
  - All v2.0 features preserved.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from core.redaction import sanitize_error_message

logger = logging.getLogger("kanyoza.services.health_monitor")


# ─────────────────────────────────────────────────────────────────────────
# Tunables (env-overridable so you can change cadence without a redeploy)
# ─────────────────────────────────────────────────────────────────────────

_DEFAULT_INTERVAL_SECONDS = int(os.getenv("HEALTH_MONITOR_INTERVAL_SECONDS", "300"))
_DEFAULT_WARMUP_SECONDS = float(os.getenv("HEALTH_MONITOR_WARMUP_SECONDS", "15"))
_ALERT_QUEUE_MAX = int(os.getenv("HEALTH_MONITOR_ALERT_QUEUE_MAX", "200"))
_PROBE_TIMEOUT_SECONDS = float(os.getenv("HEALTH_MONITOR_PROBE_TIMEOUT", "5.0"))

# Service name prefixes that, when down, should count as "critical".
# Used by health_check() to decide between healthy/degraded/unhealthy.
_CRITICAL_SERVICE_NAMES = frozenset({
    "Redis",
    "Supabase",
    "event_bus",
    "task_queue",
})


class HealthMonitorService:
    """Monitors system health — FULLY ASYNC-NATIVE.

    v4.0: queued alerts, three-state model, async Gemini probe, Redis-backed
    daily-summary dedup, registry snapshot integration.
    """

    def __init__(self, container=None, event_bus=None, platform_state_store=None):
        self.container = container
        self._event_bus = event_bus
        self._running = False

        self._monitor_task: Optional[asyncio.Task] = None
        self._alert_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        # Public state attributes (unchanged names for backward compat)
        # Values are now strings: "ok" | "unknown" | "down".
        # Consumers that treated these as booleans should switch to
        # `value == "ok"`. The old bool semantics can be restored by
        # setting HEALTH_MONITOR_LEGACY_BOOL=1 in the environment.
        self._legacy_bool_mode = os.getenv("HEALTH_MONITOR_LEGACY_BOOL", "").strip() == "1"
        self._last_health_state: Dict[str, Any] = {}
        self._last_summary_sent_date: Optional[str] = None
        self._last_health_check_time = 0.0
        self._last_health_snapshot: Dict[str, Any] = {}
        self._last_service_diagnostics: Dict[str, Any] = {}
        self._platform_state_snapshot = None
        self._platform_state_persisted_revision = None
        self._platform_state_publisher = None
        if platform_state_store is not None and event_bus is not None:
            try:
                from core.platform_state_publisher import PlatformStatePublisher
                self._platform_state_publisher = PlatformStatePublisher(
                    platform_state_store, event_bus
                )
            except Exception as exc:
                logger.warning(
                    "Platform State publisher unavailable: %s",
                    sanitize_error_message(exc),
                )

        # Alert pipeline
        self._alert_queue: "asyncio.Queue[Tuple[str, str]]" = asyncio.Queue(
            maxsize=_ALERT_QUEUE_MAX
        )

        # Statistics
        self._checks_completed = 0
        self._alerts_sent = 0
        self._recovery_alerts_sent = 0
        self._alerts_dropped = 0
        self._emit_failures = 0

        # Guards
        self._start_lock = asyncio.Lock()
        self._check_lock = asyncio.Lock()

        # Recent transitions (bounded ring for operator debugging)
        self._recent_transitions: deque = deque(maxlen=50)

    # ═══════════════════════════════════════════════════════════════
    # LIFECYCLE (ASYNC)
    # ═══════════════════════════════════════════════════════════════

    @property
    def service_name(self) -> str:
        return "health_monitor"

    async def start(self):
        async with self._start_lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
            self._monitor_task = asyncio.create_task(
                self._run_loop(),
                name="health-monitor-service",
            )
            self._alert_task = asyncio.create_task(
                self._alert_dispatch_loop(),
                name="health-monitor-alerts",
            )
        logger.info(
            "HealthMonitorService v4.0 started (interval=%ss, warmup=%ss)",
            _DEFAULT_INTERVAL_SECONDS,
            _DEFAULT_WARMUP_SECONDS,
        )

    async def stop(self):
        self._running = False
        self._stop_event.set()

        for task, name in (
            (self._monitor_task, "monitor"),
            (self._alert_task, "alerts"),
        ):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.debug("Task %s stop raised", name, exc_info=True)

        self._monitor_task = None
        self._alert_task = None
        logger.info("HealthMonitorService stopped.")

    async def check_health_async(self) -> dict:
        """Async health check for HealthProvider protocol.

        Reports the cached snapshot. Must not recurse into the registry
        while the registry is probing this provider.
        """
        return self.health_check()

    # ═══════════════════════════════════════════════════════════════
    # EVENT BUS
    # ═══════════════════════════════════════════════════════════════

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus

    async def _emit(self, event_name: str, payload: dict) -> None:
        if not self._event_bus:
            return
        try:
            publisher = getattr(self._event_bus, "publish", None)
            if publisher is None:
                return
            result = publisher(event_name, payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            self._emit_failures += 1
            logger.warning(
                "Health monitor event emit failed: %s",
                event_name,
                exc_info=True,
            )

    # ═══════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════════════

    async def _run_loop(self):
        await self._wait_or_stop(_DEFAULT_WARMUP_SECONDS)
        while self._running:
            try:
                await self._check_all_services()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Health monitor cycle failed")

            try:
                self._check_daily_summary()
            except Exception:
                logger.exception("Daily summary check failed")

            if not self._running:
                break
            await self._wait_or_stop(_DEFAULT_INTERVAL_SECONDS)

    async def _wait_or_stop(self, seconds: float) -> None:
        """Sleep for `seconds`, waking immediately if stop is signaled."""
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # ═══════════════════════════════════════════════════════════════
    # CHECK ALL SERVICES
    # ═══════════════════════════════════════════════════════════════

    async def _check_all_services(self):
        """Check all registered health providers and alert on changes."""
        if self._check_lock.locked():
            logger.debug("Previous health check still in flight; skipping cycle")
            return

        async with self._check_lock:
            cycle_start = asyncio.get_running_loop().time()
            services = await self._collect_service_health()
            # Build a read-only, dependency-aware projection from the exact
            # cached HealthReport snapshot just collected. This does not probe
            # providers a second time and does not persist or grant authority.
            try:
                from lifecycle import health_registry
                from core.platform_state_health import read_platform_state_from_health_registry

                previous_revision = (
                    self._platform_state_snapshot.revision
                    if self._platform_state_snapshot is not None else 0
                )
                infrastructure_statuses = {
                    "redis": services.get("Redis", "unknown"),
                    "supabase": services.get("Supabase", "unknown"),
                    "gemini": services.get("Gemini", "unknown"),
                }
                candidate = read_platform_state_from_health_registry(
                    registry=health_registry,
                    previous_revision=previous_revision,
                    supplemental_statuses=infrastructure_statuses,
                )
                if self._platform_state_publisher is not None:
                    try:
                        self._platform_state_snapshot = await self._platform_state_publisher.publish(
                            candidate
                        )
                        self._platform_state_persisted_revision = (
                            self._platform_state_snapshot.revision
                        )
                    except Exception as publish_exc:
                        # The in-process projection remains useful for diagnostics,
                        # but it is never represented as a durable shared snapshot.
                        self._platform_state_snapshot = candidate
                        logger.warning(
                            "Platform State durable publication unavailable: %s",
                            sanitize_error_message(publish_exc),
                        )
                else:
                    self._platform_state_snapshot = candidate
            except Exception as exc:
                logger.debug(
                    "Platform State projection unavailable: %s",
                    sanitize_error_message(exc),
                )
            duration = asyncio.get_running_loop().time() - cycle_start
            self._checks_completed += 1
            self._last_health_check_time = asyncio.get_running_loop().time()

            transitions = 0
            for name, current in services.items():
                prev = self._last_health_state.get(name)

                # Fire alerts only on real ok <-> down transitions.
                # "unknown" never alerts. This eliminates false positives
                # from probe timeouts and monitor warm-up.
                if (
                    prev is not None
                    and prev != current
                    and "unknown" not in (prev, current)
                ):
                    transitions += 1
                    await self._handle_transition(name, prev, current)

                self._last_health_state[name] = current

            # Drop services that disappeared from the current snapshot
            active = set(services.keys())
            for stale in [k for k in self._last_health_state if k not in active]:
                self._last_health_state.pop(stale, None)

            self._last_health_snapshot = dict(services)

            logger.info(
                "health_monitor.cycle.completed",
                extra={
                    "event": "health_monitor.cycle.completed",
                    "duration_s": round(duration, 3),
                    "services_total": len(services),
                    "services_ok": sum(1 for v in services.values() if v == "ok"),
                    "services_down": sum(1 for v in services.values() if v == "down"),
                    "services_unknown": sum(1 for v in services.values() if v == "unknown"),
                    # Preserve the bounded per-service snapshot so operators can
                    # identify degraded providers without enabling DEBUG logs.
                    "service_states": dict(sorted(services.items())),
                    "service_diagnostics": dict(self._last_service_diagnostics),
                    "services_down_names": sorted(
                        name for name, state in services.items() if state == "down"
                    ),
                    "services_unknown_names": sorted(
                        name for name, state in services.items() if state == "unknown"
                    ),
                    "transitions": transitions,
                },
            )

    async def _handle_transition(self, name: str, prev: str, current: str) -> None:
        """Emit alert + event for a service health state change."""
        is_recovery = current == "ok"
        emoji = "🟢" if is_recovery else "🔴"
        status_str = "ONLINE" if is_recovery else "OFFLINE"

        self._recent_transitions.append(
            {
                "service": name,
                "from": prev,
                "to": current,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )

        self._enqueue_alert(
            kind="recovery" if is_recovery else "alert",
            message=f"{emoji} <b>{name}</b>: {status_str}",
        )

        await self._emit(
            "health.state_changed",
            {
                "service": name,
                "status": "healthy" if is_recovery else "unhealthy",
                "previous": prev,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _collect_service_health(self) -> Dict[str, str]:
        """Collect health status from all available sources.

        Returns a dict of {service_name: "ok" | "unknown" | "down"}.
        """
        services: Dict[str, str] = {}

        # 1. Lifecycle health registry — prefer the cached snapshot if the
        #    registry exposes it (v-next). Fall back to check_all() otherwise.
        try:
            from lifecycle import health_registry

            cached_checker = getattr(health_registry, "check_all_cached", None)
            if callable(cached_checker):
                # Refresh after 60s; the monitor's cadence is slower, so each
                # normal monitor cycle sees a current snapshot. This method
                # publishes the reports into the registry cache as well.
                reports = cached_checker(max_age_seconds=60.0)
                if asyncio.iscoroutine(reports):
                    reports = await reports
            else:
                reader = getattr(health_registry, "read_snapshot", None)
                reports = reader() if callable(reader) else None
                if not reports:
                    maybe = health_registry.check_all()
                    if asyncio.iscoroutine(maybe):
                        maybe = await maybe
                    reports = maybe
                    publisher = getattr(health_registry, "publish_snapshot", None)
                    if callable(publisher) and reports:
                        published = publisher(reports)
                        if asyncio.iscoroutine(published):
                            await published
            self._last_service_diagnostics = {}
            for name, report in (reports or {}).items():
                services[name] = self._classify_report(report)
                if name == "meta_engine":
                    metadata = getattr(report, "metadata", {}) or {}
                    diagnostic_keys = (
                        "snapshot_status",
                        "engine_states",
                        "unknown_engines",
                        "degraded_engines",
                        "offline_engines",
                    )
                    details = {
                        key: metadata[key]
                        for key in diagnostic_keys
                        if isinstance(metadata, dict) and key in metadata
                    }
                    if details:
                        self._last_service_diagnostics[name] = details
        except Exception as exc:
            logger.debug("Health registry check failed: %s", sanitize_error_message(exc))

        # 2. Direct infrastructure probes
        services["Redis"] = await self._check_redis()
        services["Supabase"] = await self._check_supabase()
        services["Gemini"] = await self._check_gemini()

        # 3. MetaEngine snapshot
        try:
            from services.meta_engine import meta_engine
            engines = getattr(meta_engine, "_engines", None)
            if engines:
                snapshot = meta_engine.take_snapshot()
                for name, health in snapshot.engines.items():
                    status_value = getattr(getattr(health, "status", None), "value", None)
                    services[f"Engine:{name}"] = (
                        "ok" if status_value == "nominal" else "down"
                    )
        except Exception:
            pass

        # 4. Plugins
        try:
            if self.container:
                from plugins.base import PluginRegistry
                registry = self.container.try_resolve(PluginRegistry)
                if registry:
                    for plugin in registry.get_all():
                        name = plugin.name
                        checker = getattr(plugin, "health_check", None)
                        health = checker() if callable(checker) else plugin.health()
                        services[f"Plugin:{name}"] = self._classify_plugin_health(health)
        except Exception:
            pass

        return services

    @staticmethod
    def _classify_plugin_health(health: Any) -> str:
        """Preserve explicit plugin unknown/degraded states; retain legacy bool contracts."""
        if not isinstance(health, dict):
            return "unknown"
        if health.get("status") is not None:
            return HealthMonitorService._classify_report(health)
        return "ok" if health.get("ok") else "down"

    @staticmethod
    def _classify_report(report: Any) -> str:
        """Map a HealthReport (or legacy shape) to ok/unknown/down."""
        if report is None:
            return "unknown"
        status = report.get("status") if isinstance(report, dict) else getattr(report, "status", None)
        # Enum with .value; dictionary-shaped plugin contracts are also supported.
        status_value = getattr(status, "value", None)
        if status_value is None:
            status_value = str(status) if status is not None else ""
        status_str = str(status_value).lower()

        if status_str in ("healthy", "nominal", "ok", "ready", "running"):
            return "ok"
        if status_str in ("unhealthy", "offline", "failed", "critical", "stopped"):
            return "down"
        # Degraded, unknown, starting, probe failures, malformed — all "unknown"
        # from an alerting perspective.
        return "unknown"

    # ─────────────────────────────────────────────────────────────
    # Probes
    # ─────────────────────────────────────────────────────────────

    async def _check_redis(self) -> str:
        try:
            if not self.container:
                return "unknown"
            from core.redis_client import UpstashRedisClient
            redis = self.container.try_resolve(UpstashRedisClient)
            if not redis or not getattr(redis, "is_available", False):
                return "unknown"
            loop = asyncio.get_running_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, redis.ping),
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
            return "ok" if bool(result) else "down"
        except asyncio.TimeoutError:
            return "unknown"
        except Exception:
            return "down"

    async def _check_supabase(self) -> str:
        try:
            if not self.container:
                return "unknown"
            from supabase import Client as SupabaseClient
            supabase = self.container.try_resolve(SupabaseClient)
            if not supabase:
                return "unknown"
            loop = asyncio.get_running_loop()
            res = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: supabase.table("workspaces").select("id").limit(1).execute(),
                ),
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
            return "ok" if res is not None else "down"
        except asyncio.TimeoutError:
            return "unknown"
        except Exception:
            return "down"

    async def _check_gemini(self) -> str:
        """Check Gemini via AIOrchestrator health, then HTTP fallback (async)."""
        # Prefer orchestrator health — no network
        try:
            if self.container:
                from ai.orchestrator import AIOrchestrator
                orch = self.container.try_resolve(AIOrchestrator)
                if orch:
                    health = orch.health()
                    if any(h.get("available", False) for h in health.values()):
                        return "ok"
        except Exception:
            pass

        # Fallback: direct async HTTP with API key in header, not URL.
        try:
            from services.gemini import gemini
            if not gemini.api_key:
                return "unknown"
            import httpx
            async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
                resp = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash",
                    headers={"x-goog-api-key": gemini.api_key},
                )
                return "ok" if resp.status_code == 200 else "down"
        except asyncio.TimeoutError:
            return "unknown"
        except Exception:
            return "down"

    # ═══════════════════════════════════════════════════════════════
    # ALERTING
    # ═══════════════════════════════════════════════════════════════

    def _enqueue_alert(self, kind: str, message: str) -> None:
        """Queue an alert. Drops on overflow with a counter."""
        try:
            self._alert_queue.put_nowait((kind, message))
        except asyncio.QueueFull:
            self._alerts_dropped += 1
            logger.warning("Health alert queue full; dropped %s alert", kind)

    async def _alert_dispatch_loop(self) -> None:
        """Single worker: serialize Telegram sends with backpressure."""
        while self._running:
            try:
                kind, message = await self._alert_queue.get()
            except asyncio.CancelledError:
                break

            try:
                from services.email_service import email_service

                full = (
                    f"⚠️ <b>Kanyoza Health Alert</b>\n"
                    f"{message}\n"
                    f"<i>{datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>"
                )
                await email_service.send_telegram(full)

                if kind == "recovery":
                    self._recovery_alerts_sent += 1
                else:
                    self._alerts_sent += 1
            except Exception:
                logger.error("Failed to send health alert", exc_info=True)
                # Brief backoff so a downed Telegram doesn't hot-loop
                await asyncio.sleep(5.0)
            finally:
                try:
                    self._alert_queue.task_done()
                except Exception:
                    pass

    def _send_alert(self, message: str) -> None:
        """Legacy sync-callable entry point. Enqueues for async delivery."""
        self._enqueue_alert("alert", message)

    # ═══════════════════════════════════════════════════════════════
    # DAILY SUMMARY
    # ═══════════════════════════════════════════════════════════════

    def _check_daily_summary(self):
        now_utc = datetime.now(timezone.utc)
        today_str = now_utc.strftime("%Y-%m-%d")

        if self._last_summary_sent_date == today_str:
            return

        try:
            from config.settings import settings
            config_time = settings.daily_summary_time_utc or "08:00"
        except Exception:
            config_time = "08:00"

        try:
            target_hour, target_minute = map(int, config_time.split(":"))
        except Exception:
            target_hour, target_minute = 8, 0

        if now_utc.hour == target_hour and now_utc.minute == target_minute:
            # Best-effort Redis dedup so restarts don't double-send.
            if self._summary_already_sent_today(today_str):
                self._last_summary_sent_date = today_str
                return
            self._send_daily_summary()
            self._mark_summary_sent(today_str)
            self._last_summary_sent_date = today_str

    def _summary_already_sent_today(self, day: str) -> bool:
        try:
            if not self.container:
                return False
            from core.redis_client import UpstashRedisClient
            redis = self.container.try_resolve(UpstashRedisClient)
            if not redis or not getattr(redis, "is_available", False):
                return False
            return bool(redis.get(f"health_monitor:daily_summary:{day}"))
        except Exception:
            return False

    def _mark_summary_sent(self, day: str) -> None:
        try:
            if not self.container:
                return
            from core.redis_client import UpstashRedisClient
            redis = self.container.try_resolve(UpstashRedisClient)
            if not redis or not getattr(redis, "is_available", False):
                return
            # 48h TTL
            try:
                redis.set(f"health_monitor:daily_summary:{day}", "1", ex=172800)
            except TypeError:
                # Older client without kwargs
                redis.set(f"health_monitor:daily_summary:{day}", "1")
        except Exception:
            logger.debug("Daily summary dedup write failed", exc_info=True)

    def _send_daily_summary(self):
        try:
            services = self._collect_service_health_sync()
            healthy_count = sum(1 for v in services.values() if v == "ok")
            total_count = len(services)

            health_lines = []
            for name, status in services.items():
                icon = "✅" if status == "ok" else ("⚠️" if status == "unknown" else "❌")
                health_lines.append(f"• {icon} {name}")

            summary = (
                f"📊 <b>Daily System Health Report</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                f"💻 <b>Services</b>: {healthy_count}/{total_count} healthy\n\n"
                f"{chr(10).join(health_lines)}\n\n"
                f"<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>"
            )

            # Route through the alert queue so delivery is serialized.
            self._enqueue_alert("alert", summary)
            logger.info("Daily health summary enqueued.")
        except Exception:
            logger.error("Failed to generate daily summary", exc_info=True)

    def _collect_service_health_sync(self) -> Dict[str, str]:
        """Return the last collected snapshot without blocking."""
        return dict(self._last_health_snapshot)

    # ═══════════════════════════════════════════════════════════════
    # STATISTICS / HEALTH
    # ═══════════════════════════════════════════════════════════════

    def _snapshot_age_seconds(self) -> Optional[float]:
        if self._last_health_check_time <= 0:
            return None
        try:
            now = asyncio.get_event_loop().time()
            return now - self._last_health_check_time
        except Exception:
            return None

    def get_stats(self) -> dict:
        services = self._collect_service_health_sync()
        ok = sum(1 for v in services.values() if v == "ok")
        down = sum(1 for v in services.values() if v == "down")
        unknown = sum(1 for v in services.values() if v == "unknown")

        if self._legacy_bool_mode:
            # Preserve pre-v4.0 shape for callers that expect it.
            healthy_bool = {k: (v == "ok") for k, v in services.items()}
            return {
                "checks_completed": self._checks_completed,
                "alerts_sent": self._alerts_sent,
                "recovery_alerts_sent": self._recovery_alerts_sent,
                "services_monitored": len(services),
                "services_healthy": ok,
                "services_unhealthy": len(services) - ok,
                "_legacy_snapshot": healthy_bool,
            }

        return {
            "checks_completed": self._checks_completed,
            "alerts_sent": self._alerts_sent,
            "recovery_alerts_sent": self._recovery_alerts_sent,
            "alerts_dropped": self._alerts_dropped,
            "emit_failures": self._emit_failures,
            "services_monitored": len(services),
            "services_healthy": ok,
            "services_down": down,
            "services_unknown": unknown,
            "snapshot_age_seconds": self._snapshot_age_seconds(),
            "alert_queue_depth": self._alert_queue.qsize(),
            "platform_state_revision": self._platform_state_persisted_revision,
            "platform_state_projection_revision": (
                self._platform_state_snapshot.revision
                if self._platform_state_snapshot is not None else None
            ),
            "platform_state_services": (
                {
                    item.service_id: item.readiness.value
                    for item in self._platform_state_snapshot.services
                }
                if self._platform_state_snapshot is not None else {}
            ),
        }

    def get_platform_state_snapshot(self):
        """Return the latest immutable Platform State projection, if available."""
        return self._platform_state_snapshot

    def health_check(self) -> dict:
        stats = self.get_stats()

        # Before the first cycle: we cannot claim health.
        if self._checks_completed == 0:
            return {"status": "unknown", "reason": "no checks completed yet", "stats": stats}

        # Stale snapshot: 3x the interval means the loop is stuck.
        age = stats.get("snapshot_age_seconds")
        if age is not None and age > (_DEFAULT_INTERVAL_SECONDS * 3):
            return {"status": "unknown", "reason": "snapshot stale", "stats": stats}

        # Any critical service down -> unhealthy
        snapshot = self._collect_service_health_sync()
        critical_down = [
            n for n, s in snapshot.items()
            if s == "down" and n in _CRITICAL_SERVICE_NAMES
        ]
        if critical_down:
            return {
                "status": "unhealthy",
                "critical_down": critical_down,
                "stats": stats,
            }

        monitored = stats["services_monitored"] or 1
        down = stats.get("services_down", 0)
        if down / monitored > 0.3:
            return {"status": "degraded", "stats": stats}

        return {"status": "healthy", "stats": stats}


__all__ = ["HealthMonitorService"]
