"""
Health Monitor Service v3.0 — Async-Native Autonomous System Health Watchdog.

v3.0 CHANGES:
  - Fully async-native (no threading.Thread, no time.sleep)
  - asyncio.Task replaces threading.Thread
  - await asyncio.sleep() replaces time.sleep()
  - UnifiedEventBus integration
  - All v2.0 features preserved
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("kanyoza.services.health_monitor")


class HealthMonitorService:
    """Monitors system health — FULLY ASYNC-NATIVE.

    v3.0: asyncio.Task, await asyncio.sleep, UnifiedEventBus.
    """

    def __init__(self, container=None, event_bus=None):
        self.container = container
        self._event_bus = event_bus
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._last_health_state: Dict[str, bool] = {}
        self._last_summary_sent_date: Optional[str] = None
        self._last_health_check_time = 0.0
        self._last_health_snapshot: Dict[str, bool] = {}

        # Statistics
        self._checks_completed = 0
        self._alerts_sent = 0
        self._recovery_alerts_sent = 0

    # ═══════════════════════════════════════════════════════════════
    # LIFECYCLE (ASYNC)
    # ═══════════════════════════════════════════════════════════════

    @property
    def service_name(self) -> str:
        return "health_monitor"

    async def start(self):
        if self._running:
            return
        self._running = True
        self._monitor_task = asyncio.create_task(
            self._run_loop(),
            name="health-monitor-service",
        )
        logger.info("HealthMonitorService v3.0 started (async-native).")

    async def stop(self):
        self._running = False
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        self._monitor_task = None
        logger.info("HealthMonitorService stopped.")

    async def check_health_async(self) -> dict:
        """Async health check for HealthProvider protocol.

        The monitor reports its cached snapshot. It must not recursively invoke
        the lifecycle health registry while the registry is checking this provider.
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
            if hasattr(self._event_bus, "publish"):
                result = self._event_bus.publish(event_name, payload)
                if asyncio.iscoroutine(result):
                    await result
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # MAIN LOOP (ASYNC)
    # ═══════════════════════════════════════════════════════════════

    async def _run_loop(self):
        await asyncio.sleep(15)  # Warm-up delay
        while self._running:
            try:
                now_ts = asyncio.get_event_loop().time()

                # Health check every 5 minutes
                if now_ts - self._last_health_check_time >= 300:
                    self._last_health_check_time = now_ts
                    await self._check_all_services()

                # Daily summary check
                self._check_daily_summary()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Health monitor loop error: %s", e)

            # Sleep in chunks for responsive shutdown
            for _ in range(30):
                if not self._running:
                    break
                await asyncio.sleep(1)

    # ═══════════════════════════════════════════════════════════════
    # CHECK ALL SERVICES (ASYNC)
    # ═══════════════════════════════════════════════════════════════

    async def _check_all_services(self):
        """Check all registered health providers and alert on changes."""
        services = await self._collect_service_health()
        self._checks_completed += 1

        for name, current_ok in services.items():
            prev_ok = self._last_health_state.get(name)

            if prev_ok is not None and prev_ok != current_ok:
                status_str = "ONLINE" if current_ok else "OFFLINE"
                emoji = "🟢" if current_ok else "🔴"

                if current_ok:
                    self._recovery_alerts_sent += 1
                else:
                    self._alerts_sent += 1

                self._send_alert(f"{emoji} <b>{name}</b>: {status_str}")

                await self._emit(
                    "health.state_changed",
                    {
                        "service": name,
                        "status": "healthy" if current_ok else "unhealthy",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )

            self._last_health_state[name] = current_ok
        self._last_health_snapshot = dict(services)

    async def _collect_service_health(self) -> Dict[str, bool]:
        """Collect health status from all available sources (async)."""
        services = {}

        # 1. Check via lifecycle health registry
        try:
            from lifecycle import health_registry
            reports = health_registry.check_all()
            if asyncio.iscoroutine(reports):
                reports = await reports
            for name, report in (reports or {}).items():
                status = getattr(getattr(report, "status", None), "value", None) or str(
                    getattr(report, "status", "")
                )
                services[name] = str(status).lower() in ("healthy", "nominal", "ok")
        except Exception as exc:
            logger.debug("Health registry check failed: %s", exc)

        # 2. Check core infrastructure directly
        services["Redis"] = await self._check_redis()
        services["Supabase"] = await self._check_supabase()
        services["Gemini"] = self._check_gemini()

        # 3. Check via MetaEngine if available
        try:
            from services.meta_engine import meta_engine
            if meta_engine._engines:
                snapshot = meta_engine.take_snapshot()
                for name, health in snapshot.engines.items():
                    services[f"Engine:{name}"] = health.status.value == "nominal"
        except Exception:
            pass

        # 4. Check plugins
        try:
            if self.container:
                from plugins.base import PluginRegistry
                registry = self.container.try_resolve(PluginRegistry)
                if registry:
                    health = registry.health_all()
                    for name, h in health.items():
                        services[f"Plugin:{name}"] = h.get("ok", False)
        except Exception:
            pass

        return services

    async def _check_redis(self) -> bool:
        try:
            if self.container:
                from core.redis_client import UpstashRedisClient
                redis = self.container.try_resolve(UpstashRedisClient)
                if redis and redis.is_available:
                    loop = asyncio.get_event_loop()
                    return await loop.run_in_executor(None, redis.ping)
        except Exception:
            pass
        return False

    async def _check_supabase(self) -> bool:
        try:
            if self.container:
                from supabase import Client as SupabaseClient
                supabase = self.container.try_resolve(SupabaseClient)
                if supabase:
                    loop = asyncio.get_event_loop()
                    res = await loop.run_in_executor(
                        None,
                        lambda: supabase.table("workspaces").select("id").limit(1).execute(),
                    )
                    return res is not None
        except Exception:
            pass
        return False

    def _check_gemini(self) -> bool:
        """Check Gemini via AIOrchestrator health."""
        try:
            if self.container:
                from ai.orchestrator import AIOrchestrator
                orch = self.container.try_resolve(AIOrchestrator)
                if orch:
                    health = orch.health()
                    return any(h.get("available", False) for h in health.values())
        except Exception:
            pass

        # Fallback: direct check via shared HttpClient (pooled, no bare requests)
        try:
            from services.gemini import gemini
            from utils.http_client import HttpClient
            if gemini.api_key:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash?key={gemini.api_key}"
                http = HttpClient("gemini_health", default_timeout=10.0)
                resp = http.get(url)
                return resp.status_code == 200
        except Exception:
            pass
        return False

    # ═══════════════════════════════════════════════════════════════
    # ALERTING (unchanged — sync is fine for HTTP)
    # ═══════════════════════════════════════════════════════════════

    def _send_alert(self, message: str) -> None:
        """Send alert via EmailService (Telegram). Fire-and-forget safe for sync callers."""
        try:
            from services.email_service import email_service
            msg = (
                f"⚠️ <b>Kanyoza Health Alert</b>\n"
                f"{message}\n"
                f"<i>{datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>"
            )
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(email_service.send_telegram(msg))
            except RuntimeError:
                # No running loop — best-effort sync bridge
                try:
                    asyncio.run(email_service.send_telegram(msg))
                except Exception as inner:
                    logger.error("Failed to send health alert (no loop): %s", inner)
        except Exception as exc:
            logger.error("Failed to send health alert: %s", exc)

    # ═══════════════════════════════════════════════════════════════
    # DAILY SUMMARY (unchanged)
    # ═══════════════════════════════════════════════════════════════

    def _check_daily_summary(self):
        now_utc = datetime.now(timezone.utc)
        today_str = now_utc.strftime("%Y-%m-%d")

        if self._last_summary_sent_date == today_str:
            return

        from config.settings import settings
        config_time = settings.daily_summary_time_utc or "08:00"
        try:
            target_hour, target_minute = map(int, config_time.split(":"))
        except Exception:
            target_hour, target_minute = 8, 0

        if now_utc.hour == target_hour and now_utc.minute == target_minute:
            self._send_daily_summary()
            self._last_summary_sent_date = today_str

    def _send_daily_summary(self):
        try:
            services = self._collect_service_health_sync()
            healthy_count = sum(1 for ok in services.values() if ok)
            total_count = len(services)

            health_lines = []
            for name, ok in services.items():
                icon = "✅" if ok else "❌"
                health_lines.append(f"• {icon} {name}")

            summary = (
                f"📊 <b>Daily System Health Report</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                f"💻 <b>Services</b>: {healthy_count}/{total_count} healthy\n\n"
                f"{chr(10).join(health_lines)}\n\n"
                f"<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>"
            )

            try:
                from services.email_service import email_service
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(email_service.send_telegram(summary))
                except RuntimeError:
                    asyncio.run(email_service.send_telegram(summary))
                logger.info("Daily health summary sent.")
            except Exception as exc:
                logger.error("Failed to send daily summary: %s", exc)

        except Exception as e:
            logger.error("Failed to generate daily summary: %s", e)

    def _collect_service_health_sync(self) -> Dict[str, bool]:
        """Return the last asynchronously collected snapshot without blocking the event loop."""
        return dict(self._last_health_snapshot)

    # ═══════════════════════════════════════════════════════════════
    # STATISTICS
    # ═══════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        services = self._collect_service_health_sync()
        healthy = sum(1 for ok in services.values() if ok)
        return {
            "checks_completed": self._checks_completed,
            "alerts_sent": self._alerts_sent,
            "recovery_alerts_sent": self._recovery_alerts_sent,
            "services_monitored": len(services),
            "services_healthy": healthy,
            "services_unhealthy": len(services) - healthy,
        }

    def health_check(self) -> dict:
        stats = self.get_stats()
        status = "healthy"
        if stats["services_unhealthy"] > stats["services_monitored"] * 0.3:
            status = "degraded"
        return {"status": status, "stats": stats}