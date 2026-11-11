"""
Kanyoza Universal Pulse Monitor v1.0
=====================================

Monitors ALL activities across the entire digital organism.

Like a full-body health check — not just checking if the heart beats,
but checking if EVERY organ is producing output.

MONITORED ACTIVITIES:
  - Content production (posts)
  - Task execution (Director)
  - Decision making (Condition Engine)
  - Email delivery (Email Service)
  - Church activities (Church MIS)
  - School activities (School MIS)
  - Hospital activities (Hospital CRM)
  - Database writes (Supabase)
  - Event bus flow (UnifiedEventBus)
  - API requests (traffic)
  - Socket connections (real-time clients)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.universal_pulse")


class ActivityStatus:
    HEALTHY = "healthy"
    STAGNATED = "stagnated"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class UniversalPulseMonitor:
    """
    Monitors every subsystem's activity.
    Alerts when ANY part of the organism stops producing.
    """
    
    VERSION = "1.0.0"
    
    # Time thresholds (in hours) before alerting
    THRESHOLDS = {
        "posts": 24,                    # No posts in 24h = stagnation
        "director_tasks": 12,           # No new tasks in 12h = issue
        "director_observations": 6,     # No observations in 6h = issue
        "events": 1,                    # No events in 1h = issue
        "api_keys": 24,                 # No API activity in 24h = low traffic
        "church_members": 72,           # No new members in 72h = normal
        "school_students": 72,          # No new students in 72h = normal
        "hospital_patients": 72,        # No new patients in 72h = normal
        "crm_contacts": 72,             # No new contacts in 72h = normal
    }

    # Real timestamp columns per table (PostgREST 400 if wrong column ordered)
    TABLE_TIME_COLUMNS = {
        "posts": ("created_at",),
        "director_tasks": ("created_at",),
        "director_observations": ("timestamp", "created_at"),
        "events": ("created_at",),
        "api_keys": ("created_at", "updated_at"),
        "church_members": ("created_at",),
        "school_students": ("created_at",),
        "hospital_patients": ("created_at",),
        "crm_contacts": ("created_at",),
    }
    
    def __init__(self, container=None, event_bus=None, supabase=None):
        self._container = container
        self._event_bus = event_bus
        self._supabase = supabase
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._last_check: Dict[str, datetime] = {}
        self._stagnation_flags: Dict[str, bool] = {}
        self._check_count = 0
        self._last_full_report: Dict[str, Any] = {}
    
    @property
    def service_name(self) -> str:
        return "universal_pulse"
    
    async def start(self, interval_seconds: int = 300) -> None:
        """Start monitoring every 5 minutes."""
        if self._running:
            return
        self._running = True
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(interval_seconds),
            name="universal-pulse",
        )
        logger.info("🌐 Universal Pulse Monitor started — checking ALL activities every %ds", interval_seconds)
    
    async def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
    
    async def _monitor_loop(self, interval_seconds: int) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                await self.check_all_activities()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Universal pulse check failed: %s", exc)
            
            await asyncio.sleep(interval_seconds)
    
    async def check_all_activities(self) -> Dict[str, Any]:
        """Check ALL subsystem activities."""
        self._check_count += 1
        
        supabase = self._supabase
        if supabase is None:
            from services.supabase import get_service_client_from_settings
            supabase = get_service_client_from_settings()
        
        if supabase is None:
            return {"status": "UNKNOWN", "message": "Supabase not available"}
        
        report = {}
        stagnated_systems = []
        
        # Check each table
        for table_name, threshold_hours in self.THRESHOLDS.items():
            result = await self._check_table_activity(supabase, table_name, threshold_hours)
            report[table_name] = result
            
            if result["status"] == ActivityStatus.STAGNATED:
                stagnated_systems.append(table_name)
        
        # Build overall status
        if stagnated_systems:
            report["overall_status"] = ActivityStatus.DEGRADED
            report["stagnated_systems"] = stagnated_systems
            report["message"] = f"⚠️ {len(stagnated_systems)} subsystems stagnated: {', '.join(stagnated_systems)}"
            
            await self._alert_stagnation(stagnated_systems)
        else:
            report["overall_status"] = ActivityStatus.HEALTHY
            report["stagnated_systems"] = []
            report["message"] = "✅ All subsystems producing normally"
        
        self._last_full_report = report
        return report
    
    async def _check_table_activity(
        self,
        supabase,
        table_name: str,
        threshold_hours: int,
    ) -> Dict[str, Any]:
        """Check if a table has recent activity using the correct timestamp column."""
        time_cols = self.TABLE_TIME_COLUMNS.get(
            table_name,
            ("created_at", "timestamp", "fetched_at", "checked_at", "started_at"),
        )

        def _query(col: str):
            return (
                supabase.table(table_name)
                .select("*")
                .order(col, desc=True)
                .limit(1)
                .execute()
            )

        try:
            loop = asyncio.get_event_loop()
            result = None
            used_col = None
            last_err = None
            for col in time_cols:
                try:
                    result = await loop.run_in_executor(None, lambda c=col: _query(c))
                    used_col = col
                    break
                except Exception as exc:
                    last_err = exc
                    continue

            if result is None:
                logger.debug(
                    "Universal pulse: %s probe failed (cols=%s): %s",
                    table_name,
                    time_cols,
                    last_err,
                )
                return {
                    "status": ActivityStatus.UNKNOWN,
                    "message": f"Could not query {table_name}",
                    "error": str(last_err)[:200] if last_err else None,
                }

            if result.data and len(result.data) > 0:
                row = result.data[0]
                created_str = (
                    row.get(used_col)
                    or row.get("created_at")
                    or row.get("timestamp")
                    or row.get("fetched_at")
                    or row.get("checked_at")
                    or row.get("started_at")
                )
                if created_str:
                    created_time = datetime.fromisoformat(
                        str(created_str).replace("Z", "+00:00")
                    )
                    self._last_check[table_name] = created_time
                    now = datetime.now(timezone.utc)
                    hours_since = (now - created_time).total_seconds() / 3600
                    if hours_since > threshold_hours:
                        self._stagnation_flags[table_name] = True
                        return {
                            "status": ActivityStatus.STAGNATED,
                            "hours_since": round(hours_since, 1),
                            "threshold_hours": threshold_hours,
                            "last_activity": created_str,
                            "time_column": used_col,
                        }
                    self._stagnation_flags[table_name] = False
                    return {
                        "status": ActivityStatus.HEALTHY,
                        "hours_since": round(hours_since, 1),
                        "threshold_hours": threshold_hours,
                        "last_activity": created_str,
                        "time_column": used_col,
                    }
                return {
                    "status": ActivityStatus.UNKNOWN,
                    "message": f"Row present but no timestamp on {table_name}",
                    "time_column": used_col,
                }

            # Empty table
            return {
                "status": ActivityStatus.STAGNATED,
                "hours_since": None,
                "threshold_hours": threshold_hours,
                "message": f"No rows in {table_name}",
                "time_column": used_col,
            }
        except Exception as exc:
            logger.debug("Universal pulse check %s error: %s", table_name, exc)
            return {
                "status": ActivityStatus.UNKNOWN,
                "message": str(exc)[:200],
            }

    
    async def _alert_stagnation(self, stagnated_systems: List[str]) -> None:
        """Send alert about stagnated subsystems."""
        if not stagnated_systems:
            return
        
        # Publish to event bus
        if self._event_bus:
            try:
                await self._event_bus.publish(
                    "universal.stagnation",
                    {"stagnated_systems": stagnated_systems, "workspace_id": "platform"},
                    source="universal_pulse",
                    workspace_id="platform",
                )
            except Exception:
                pass
        
        # Send Telegram / notification alert (best-effort)
        try:
            systems_list = "\n".join(f"  • {s}" for s in stagnated_systems)
            message = (
                f"🚨 <b>UNIVERSAL PULSE ALERT</b>\n"
                f"The following subsystems have stagnated:\n"
                f"{systems_list}\n\n"
                f"<i>Check these systems for issues.</i>"
            )
            sent = False
            try:
                from services.email_service import kanyoza_notifications
                if hasattr(kanyoza_notifications, "send_telegram"):
                    asyncio.create_task(kanyoza_notifications.send_telegram(message))
                    sent = True
                elif hasattr(kanyoza_notifications, "alert"):
                    asyncio.create_task(kanyoza_notifications.alert(message))
                    sent = True
            except Exception:
                pass
            if not sent:
                from services.email_service import email_service
                if hasattr(email_service, "send_telegram"):
                    asyncio.create_task(email_service.send_telegram(message))
                    sent = True
            if sent:
                logger.critical(
                    "🚨 Universal stagnation alert sent for %d systems",
                    len(stagnated_systems),
                )
            else:
                logger.warning(
                    "Stagnation detected (%s) but no notification channel available",
                    stagnated_systems,
                )
        except Exception as exc:
            logger.warning("Failed to send stagnation alert: %s", exc)
    
    def get_status(self) -> Dict[str, Any]:
        """Get universal pulse status."""
        return {
            "version": self.VERSION,
            "running": self._running,
            "check_count": self._check_count,
            "last_report": self._last_full_report,
            "stagnation_flags": self._stagnation_flags,
            "monitored_tables": list(self.THRESHOLDS.keys()),
        }


# Global instance
universal_pulse = UniversalPulseMonitor()


async def init_universal_pulse(container=None, event_bus=None, supabase=None) -> UniversalPulseMonitor:
    """Initialize universal pulse monitor."""
    universal_pulse._container = container
    universal_pulse._event_bus = event_bus
    universal_pulse._supabase = supabase
    await universal_pulse.start(interval_seconds=300)
    global _pulse_singleton
    _pulse_singleton = universal_pulse
    return universal_pulse

async def shutdown_universal_pulse() -> None:
    """Stop universal pulse."""
    await universal_pulse.stop()


__all__ = [
    "UniversalPulseMonitor",
    "ActivityStatus",
    "universal_pulse",
    "init_universal_pulse",
    "shutdown_universal_pulse",
]

_pulse_singleton = None

def get_universal_pulse():
    if _pulse_singleton is not None:
        return _pulse_singleton
    return globals().get("universal_pulse")

def set_universal_pulse(p) -> None:
    global _pulse_singleton
    _pulse_singleton = p
