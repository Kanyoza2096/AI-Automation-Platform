"""
Monitor Plugin — Autonomous system administrator.
Checks websites and APIs, alerts on failure.
"""

import logging
import threading
import time
import requests
from datetime import datetime, timezone
from typing import List, Dict, Any

logger = logging.getLogger("kanyoza.plugins.monitor")


class MonitorPlugin:
    """Watches external websites and services."""

    def __init__(self, supabase_client=None, redis_client=None):
        self.supabase = supabase_client
        self.redis = redis_client
        self.name = "monitor"
        self._running = False
        self._thread = None

    def get_commands(self) -> List[Dict[str, Any]]:
        return [
            {"name": "monitor_status", "description": "Get monitoring status", "handler": self._handle_status},
            {"name": "monitor_check_all", "description": "Run all checks now", "handler": self._handle_check_all},
        ]

    def get_webhooks(self) -> List[Dict[str, Any]]:
        return []

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="monitor-plugin")
        self._thread.start()
        logger.info("MonitorPlugin: started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("MonitorPlugin: stopped")

    def _loop(self):
        while self._running:
            try:
                self._check_all_targets()
            except Exception as e:
                logger.error("MonitorPlugin error: %s", e)
            time.sleep(60)

    def _check_all_targets(self):
        if not self.supabase:
            return
        targets = self.supabase.table("monitor_targets").select("*").eq("is_paused", False).execute()
        for target in (targets.data or []):
            self._check_target(target)

    def _check_target(self, target: dict):
        url = target["url"]
        start = time.monotonic()
        try:
            resp = requests.get(url, timeout=10, allow_redirects=True)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            status = "up" if resp.status_code == target.get("expected_status", 200) else "down"
            
            self.supabase.table("monitor_checks").insert({
                "target_id": target["id"],
                "status": status,
                "http_status": resp.status_code,
                "response_time_ms": elapsed_ms,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }).execute()

            if status == "down":
                self._alert(target, f"HTTP {resp.status_code} (expected {target.get('expected_status', 200)})")
        except Exception as e:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            self.supabase.table("monitor_checks").insert({
                "target_id": target["id"],
                "status": "down",
                "response_time_ms": elapsed_ms,
                "error_message": str(e)[:500],
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
            self._alert(target, str(e)[:200])

    def _alert(self, target: dict, message: str):
        logger.warning("MonitorPlugin: %s is DOWN — %s", target["name"], message)
        try:
            from services.director import director
            if director and hasattr(director, '_event'):
                director._event("monitor_alert", {
                    "target": target["name"],
                    "url": target["url"],
                    "error": message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        except Exception:
            pass

    def _handle_status(self, params: dict) -> dict:
        if not self.supabase:
            return {"ok": False, "error": "Database not available"}
        targets = self.supabase.table("monitor_targets").select("*").execute()
        return {"ok": True, "targets": targets.data or []}

    def _handle_check_all(self, params: dict) -> dict:
        self._check_all_targets()
        return {"ok": True, "message": "All checks triggered"}


monitor_plugin: MonitorPlugin = None


def init_monitor_plugin(supabase_client=None, redis_client=None) -> MonitorPlugin:
    global monitor_plugin
    if monitor_plugin is None:
        monitor_plugin = MonitorPlugin(supabase_client, redis_client)
        monitor_plugin.start()
    return monitor_plugin
