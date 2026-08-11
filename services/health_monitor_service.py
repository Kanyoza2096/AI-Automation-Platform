"""
Health Monitor Service v2.0 — Autonomous system health watchdog.

v2.0 UPGRADES:
  - Checks ALL registered health providers, not just 3 hardcoded services
  - EventBus integration (health state changes published)
  - Uses EmailService for Telegram (no duplicate HTTP logic)
  - No tight coupling to internal module state
  - Health statistics tracking
  - Configurable check intervals
"""

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(
    "kanyoza.services.health_monitor"
)


class HealthMonitorService:
    """Monitors system health, sends Telegram alerts on failure,
    and compiles a daily report.

    v2.0: Uses EventBus and EmailService, checks all providers.
    """

    def __init__(self, container=None, event_bus=None):
        self.container = container
        self._event_bus = event_bus
        self._running = False
        self._thread: Optional[threading.Thread] = (
            None
        )
        self._last_health_state: Dict[str, bool] = {}
        self._last_summary_sent_date: Optional[str] = (
            None
        )
        self._last_health_check_time = 0.0

        # v2.0 — statistics
        self._checks_completed = 0
        self._alerts_sent = 0
        self._recovery_alerts_sent = 0

    # ═══════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="health-monitor-service",
        )
        self._thread.start()
        logger.info(
            "HealthMonitorService v2.0 started."
        )

    def stop(self):
        self._running = False
        if (
            self._thread
            and self._thread.is_alive()
        ):
            self._thread.join(timeout=5)
        logger.info(
            "HealthMonitorService stopped."
        )

    # ═══════════════════════════════════════════════════════════════
    # EVENT BUS
    # ═══════════════════════════════════════════════════════════════

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus

    def _emit(
        self, event_name: str, payload: dict
    ) -> None:
        if not self._event_bus:
            return
        try:
            if hasattr(self._event_bus, "publish"):
                self._event_bus.publish(
                    event_name, payload
                )
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════════════

    def _run_loop(self):
        time.sleep(15)  # Warm-up delay
        while self._running:
            try:
                now_ts = time.time()

                # Health check every 5 minutes
                if (
                    now_ts - self._last_health_check_time
                    >= 300
                ):
                    self._last_health_check_time = (
                        now_ts
                    )
                    self._check_all_services()

                # Daily summary check
                self._check_daily_summary()

            except Exception as e:
                logger.error(
                    "Health monitor loop error: %s", e
                )

            for _ in range(30):
                if not self._running:
                    break
                time.sleep(1)

    # ═══════════════════════════════════════════════════════════════
    # v2.0 — CHECK ALL SERVICES
    # ═══════════════════════════════════════════════════════════════

    def _check_all_services(self):
        """Check all registered health providers and alert on changes."""
        services = self._collect_service_health()
        self._checks_completed += 1

        for name, current_ok in services.items():
            prev_ok = self._last_health_state.get(name)

            if prev_ok is not None and prev_ok != current_ok:
                # State transition
                status_str = (
                    "ONLINE ✅"
                    if current_ok
                    else "OFFLINE ❌"
                )
                emoji = "🟢" if current_ok else "🔴"

                if current_ok:
                    self._recovery_alerts_sent += 1
                else:
                    self._alerts_sent += 1

                self._send_alert(
                    f"{emoji} <b>{name}</b>: {status_str}"
                )

                self._emit(
                    "health.state_changed",
                    {
                        "service": name,
                        "status": (
                            "healthy"
                            if current_ok
                            else "unhealthy"
                        ),
                        "timestamp": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    },
                )

            self._last_health_state[name] = current_ok

    def _collect_service_health(
        self,
    ) -> Dict[str, bool]:
        """Collect health status from all available sources."""
        services = {}

        # 1. Check via lifecycle health registry
        try:
            from lifecycle import health_registry

            reports = health_registry.check_all()
            for name, report in reports.items():
                services[name] = (
                    report.status.value == "healthy"
                )
        except Exception as exc:
            logger.debug(
                "Health registry check failed: %s", exc
            )

        # 2. Check core infrastructure directly
        services["Redis"] = self._check_redis()
        services["Supabase"] = self._check_supabase()
        services["Gemini"] = self._check_gemini()

        # 3. Check via MetaEngine if available
        try:
            from services.meta_engine import (
                meta_engine,
            )

            if meta_engine._engines:
                snapshot = meta_engine.take_snapshot()
                for (
                    name,
                    health,
                ) in snapshot.engines.items():
                    services[
                        f"Engine:{name}"
                    ] = (
                        health.status.value
                        == "nominal"
                    )
        except Exception:
            pass

        # 4. Check plugins
        try:
            if self.container:
                from plugins.base import (
                    PluginRegistry,
                )

                registry = (
                    self.container.try_resolve(
                        PluginRegistry
                    )
                )
                if registry:
                    health = registry.health_all()
                    for name, h in health.items():
                        services[
                            f"Plugin:{name}"
                        ] = h.get("ok", False)
        except Exception:
            pass

        return services

    def _check_redis(self) -> bool:
        try:
            if self.container:
                from core.redis_client import (
                    UpstashRedisClient,
                )

                redis = self.container.try_resolve(
                    UpstashRedisClient
                )
                if redis and redis.is_available:
                    return redis.ping()
        except Exception:
            pass
        return False

    def _check_supabase(self) -> bool:
        try:
            if self.container:
                from supabase import (
                    Client as SupabaseClient,
                )

                supabase = (
                    self.container.try_resolve(
                        SupabaseClient
                    )
                )
                if supabase:
                    res = (
                        supabase.table("workspaces")
                        .select("id")
                        .limit(1)
                        .execute()
                    )
                    return res is not None
        except Exception:
            pass
        return False

    def _check_gemini(self) -> bool:
        """Check Gemini via the AIOrchestrator health."""
        try:
            if self.container:
                from ai.orchestrator import (
                    AIOrchestrator,
                )

                orch = self.container.try_resolve(
                    AIOrchestrator
                )
                if orch:
                    health = orch.health()
                    return any(
                        h.get("available", False)
                        for h in health.values()
                    )
        except Exception:
            pass

        # Fallback: direct check
        try:
            import requests
            from services.gemini import gemini

            if gemini.api_key:
                url = (
                    "https://generativelanguage.googleapis.com/"
                    f"v1beta/models/gemini-2.5-flash"
                    f"?key={gemini.api_key}"
                )
                resp = requests.get(url, timeout=10)
                return resp.status_code == 200
        except Exception:
            pass
        return False

    # ═══════════════════════════════════════════════════════════════
    # ALERTING
    # ═══════════════════════════════════════════════════════════════

    def _send_alert(self, message: str) -> None:
        """Send alert via EmailService (Telegram)."""
        try:
            from services.email_service import (
                email_service,
            )

            email_service.send_telegram(
                f"⚠️ <b>Kanyoza Health Alert</b>\n"
                f"{message}\n"
                f"<i>{datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>"
            )
        except Exception as exc:
            logger.error(
                "Failed to send health alert: %s", exc
            )

    # ═══════════════════════════════════════════════════════════════
    # DAILY SUMMARY
    # ═══════════════════════════════════════════════════════════════

    def _check_daily_summary(self):
        now_utc = datetime.now(timezone.utc)
        today_str = now_utc.strftime("%Y-%m-%d")

        if self._last_summary_sent_date == today_str:
            return

        from config.settings import settings

        config_time = (
            settings.daily_summary_time_utc or "08:00"
        )
        try:
            target_hour, target_minute = map(
                int, config_time.split(":")
            )
        except Exception:
            target_hour, target_minute = 8, 0

        if (
            now_utc.hour == target_hour
            and now_utc.minute == target_minute
        ):
            self._send_daily_summary()
            self._last_summary_sent_date = today_str

    def _send_daily_summary(self):
        try:
            services = self._collect_service_health()
            healthy_count = sum(
                1 for ok in services.values() if ok
            )
            total_count = len(services)

            health_lines = []
            for name, ok in services.items():
                icon = "✅" if ok else "❌"
                health_lines.append(
                    f"• {icon} {name}"
                )

            summary = (
                f"📊 <b>Daily System Health Report</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                f"💻 <b>Services</b>: "
                f"{healthy_count}/{total_count} healthy\n\n"
                f"{chr(10).join(health_lines)}\n\n"
                f"<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>"
            )

            try:
                from services.email_service import (
                    email_service,
                )

                email_service.send_telegram(summary)
                logger.info(
                    "Daily health summary sent."
                )
            except Exception as exc:
                logger.error(
                    "Failed to send daily summary: %s",
                    exc,
                )

        except Exception as e:
            logger.error(
                "Failed to generate daily summary: %s",
                e,
            )

    # ═══════════════════════════════════════════════════════════════
    # STATISTICS
    # ═══════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        services = self._collect_service_health()
        healthy = sum(
            1 for ok in services.values() if ok
        )
        return {
            "checks_completed": self._checks_completed,
            "alerts_sent": self._alerts_sent,
            "recovery_alerts_sent": (
                self._recovery_alerts_sent
            ),
            "services_monitored": len(services),
            "services_healthy": healthy,
            "services_unhealthy": len(services)
            - healthy,
        }

    def health_check(self) -> dict:
        stats = self.get_stats()
        status = "healthy"
        if (
            stats["services_unhealthy"]
            > stats["services_monitored"] * 0.3
        ):
            status = "degraded"
        return {"status": status, "stats": stats}
