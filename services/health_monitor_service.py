# services/health_monitor_service.py
import logging
import threading
import time
import requests
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from config.settings import settings

logger = logging.getLogger("kanyoza.services.health_monitor")


class HealthMonitorService:
    """Monitors system health, sends Telegram alerts on failure, and compiles a daily report."""

    def __init__(self, container=None):
        self.container = container
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_health_state: Dict[str, bool] = {}
        self._last_summary_sent_date: Optional[str] = None
        self._last_health_check_time = 0.0

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="health-monitor-service"
        )
        self._thread.start()
        logger.info("HealthMonitorService started.")

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("HealthMonitorService stopped.")

    def _run_loop(self):
        # Wait a bit on boot to let database and infrastructure warm up
        time.sleep(15)
        while self._running:
            try:
                now_ts = time.time()
                # 1. Health Alert Outage Check (every 5 minutes)
                if now_ts - self._last_health_check_time >= 300:
                    self._last_health_check_time = now_ts
                    self._check_services_health()

                # 2. Daily Summary Report Check (every minute)
                self._check_daily_summary()

            except Exception as e:
                logger.error("Error in health monitor service loop: %s", e)

            # Sleep in small increments to respond to stop/termination quickly
            for _ in range(30):
                if not self._running:
                    break
                time.sleep(1)

    # ── Outage Checkers ───────────────────────────────────────────────

    def _check_services_health(self):
        redis_ok = self._check_redis()
        supabase_ok = self._check_supabase()
        gemini_ok = self._check_gemini()

        # Service lists to iterate
        services = [
            ("Redis", redis_ok),
            ("Supabase", supabase_ok),
            ("Gemini", gemini_ok),
        ]

        bot_token = settings.telegram_bot_token
        alert_chat_id = settings.health_alert_chat_id or settings.telegram_chat_id

        if not bot_token or not alert_chat_id:
            # Not configured for Telegram alerts, skip
            return

        for name, current_ok in services:
            prev_ok = self._last_health_state.get(name)
            if prev_ok is not None and prev_ok != current_ok:
                # State transitioned!
                status_str = "ONLINE ✅" if current_ok else "OFFLINE ❌"
                msg = f"⚠️ *[Kanyoza AI Alert]* Service status changed:\n• *Service*: {name}\n• *Status*: {status_str}"
                self._send_telegram(bot_token, alert_chat_id, msg)

                # Add to audit log
                try:
                    from api.v1 import _append_audit
                    _append_audit(
                        action="health_alert",
                        resource=name.lower(),
                        status="success" if current_ok else "failed",
                        detail=f"Service status changed to {status_str}",
                    )
                except Exception:
                    pass

            self._last_health_state[name] = current_ok

    def _check_redis(self) -> bool:
        try:
            if self.container:
                from core.redis_client import UpstashRedisClient
                redis = self.container.try_resolve(UpstashRedisClient)
                if redis:
                    return redis.ping()
        except Exception:
            pass
        return False

    def _check_supabase(self) -> bool:
        try:
            if self.container:
                from supabase import Client as SupabaseClient
                supabase = self.container.try_resolve(SupabaseClient)
                if supabase:
                    # Run a cheap exact count query on workspaces
                    res = supabase.table("workspaces").select("id").limit(1).execute()
                    return res is not None
        except Exception:
            pass
        return False

    def _check_gemini(self) -> bool:
        try:
            from services.gemini import gemini
            if gemini.api_key:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash?key={gemini.api_key}"
                resp = requests.get(url, timeout=10)
                return resp.status_code == 200
        except Exception:
            pass
        return False

    # ── Daily Summary Report ──────────────────────────────────────────

    def _check_daily_summary(self):
        bot_token = settings.telegram_bot_token
        chat_id = settings.telegram_chat_id
        if not bot_token or not chat_id:
            return

        now_utc = datetime.now(timezone.utc)
        today_str = now_utc.strftime("%Y-%m-%d")

        # Skip if already sent today
        if self._last_summary_sent_date == today_str:
            return

        # Check if the time matches configured daily summary time
        config_time = settings.daily_summary_time_utc or "08:00"
        try:
            target_hour, target_minute = map(int, config_time.split(":"))
        except Exception:
            target_hour, target_minute = 8, 0

        if now_utc.hour == target_hour and now_utc.minute == target_minute:
            self._send_daily_summary(bot_token, chat_id)
            self._last_summary_sent_date = today_str

    def _send_daily_summary(self, bot_token: str, chat_id: str):
        try:
            posts_yesterday = self._get_posts_published_yesterday()
            api_calls_yesterday = self._get_api_calls_yesterday()
            errors_24h = self._get_errors_24h()
            active_plugins = self._get_active_plugins_count()

            redis_ok = self._check_redis()
            supabase_ok = self._check_supabase()
            gemini_ok = self._check_gemini()

            health_str = (
                f"• *Redis*: {'Healthy ✅' if redis_ok else 'Offline ❌'}\n"
                f"• *Supabase*: {'Healthy ✅' if supabase_ok else 'Offline ❌'}\n"
                f"• *Gemini*: {'Healthy ✅' if gemini_ok else 'Offline ❌'}"
            )

            err_summary = ""
            if errors_24h:
                err_summary = "\n🚨 *Errors in Last 24 Hours*:\n" + "\n".join(
                    f"• {e}" for e in errors_24h[:10]
                )
                if len(errors_24h) > 10:
                    err_summary += f"\n• ... and {len(errors_24h) - 10} more errors."
            else:
                err_summary = "\n🎉 *Errors in Last 24 Hours*: None!"

            summary_msg = (
                f"📊 *Daily System Summary Report*\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                f"📝 *Posts Published Yesterday*: {posts_yesterday}\n"
                f"🔌 *API Requests Yesterday*: {api_calls_yesterday}\n"
                f"🧩 *Active Plugins*: {active_plugins}\n\n"
                f"💻 *Current System Health*:\n{health_str}\n"
                f"{err_summary}"
            )

            self._send_telegram(bot_token, chat_id, summary_msg)
            logger.info("Sent daily health summary to Telegram.")

        except Exception as e:
            logger.error("Failed to generate/send daily summary: %s", e)

    def _get_posts_published_yesterday(self) -> int:
        yesterday_start = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        yesterday_end = yesterday_start + timedelta(days=1)

        try:
            if self.container:
                from repositories.analytics_repository import AnalyticsRepository
                repo = self.container.try_resolve(AnalyticsRepository)
                if repo:
                    client = repo._get_client()
                    if client:
                        res = (
                            client.table("analytics_events")
                            .select("id")
                            .eq("event", "post_published")
                            .gte("recorded_at", yesterday_start.isoformat())
                            .lt("recorded_at", yesterday_end.isoformat())
                            .execute()
                        )
                        if res and res.data:
                            return len(res.data)
                    return sum(
                        1
                        for e in repo._buffer
                        if e.get("event") == "post_published"
                        and yesterday_start.isoformat()
                        <= e.get("recorded_at", "")
                        < yesterday_end.isoformat()
                    )
        except Exception as e:
            logger.error("Failed to query posts published yesterday: %s", e)
        return 0

    def _get_api_calls_yesterday(self) -> int:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            if self.container:
                from core.redis_client import UpstashRedisClient
                redis = self.container.try_resolve(UpstashRedisClient)
                if redis and redis.is_available:
                    val = redis.get(f"metrics:api_calls:{yesterday}")
                    if val:
                        return int(val)
        except Exception:
            pass

        local_counts = globals().get("_local_api_call_counts", {})
        return local_counts.get(yesterday, 0)

    def _get_errors_24h(self) -> List[str]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        errors = []

        # Get from memory
        try:
            from api.v1 import _audit_log, _audit_log_lock
            with _audit_log_lock:
                for entry in _audit_log:
                    if entry.get("timestamp", "") >= cutoff and entry.get("status") in (
                        "failed",
                        "error",
                        "warning",
                    ):
                        errors.append(
                            f"{entry.get('action')}: {entry.get('detail') or entry.get('resource')}"
                        )
        except Exception:
            pass

        # Get from database
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                res = (
                    supabase_service.client.table("audit_logs")
                    .select("action, detail, resource")
                    .in_("status", ["failed", "error", "warning"])
                    .gte("timestamp", cutoff)
                    .execute()
                )
                if res and res.data:
                    for row in res.data:
                        err_str = f"{row.get('action')}: {row.get('detail') or row.get('resource')}"
                        if err_str not in errors:
                            errors.append(err_str)
        except Exception:
            pass

        return errors

    def _get_active_plugins_count(self) -> int:
        industry_count = 0
        connector_count = 0
        try:
            if self.container:
                industry_count = len(getattr(self.container, "industry_plugins", {}))
                from plugins.base import PluginRegistry
                registry = self.container.try_resolve(PluginRegistry)
                if registry:
                    connector_count = len(registry.plugins)
        except Exception:
            pass
        return industry_count + connector_count

    # ── Telegram Sender Helper ────────────────────────────────────────

    def _send_telegram(self, bot_token: str, chat_id: str, text: str):
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            resp = requests.post(
                url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=15
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error("Failed to send Telegram message: %s", e)
