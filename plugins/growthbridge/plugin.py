"""
Growthbridge Plugin — Youth Business Ecosystem.
Registered with the Kanyoza AI Platform as a standalone plugin.

v2.0:
  - Added check_health_async() for HealthProvider protocol
  - Added shutdown() for ShutdownRegistry
  - Added service_name property
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("kanyoza.plugins.growthbridge")


class GrowthbridgePlugin:
    """Growthbridge business ecosystem plugin."""

    def __init__(self, supabase_client=None, redis_client=None):
        self.supabase = supabase_client
        self.redis = redis_client
        self.name = "growthbridge"
        self._running = False

    # ═══════════════════════════════════════════════════════
    # HEALTH PROVIDER PROTOCOL
    # ═══════════════════════════════════════════════════════

    @property
    def service_name(self) -> str:
        return "growthbridge_plugin"

    async def check_health_async(self) -> dict:
        """Async health check."""
        return {
            "status": "healthy" if self.supabase else "degraded",
            "name": self.name,
            "commands": len(self.get_commands()),
        }

    def check_health(self) -> dict:
        """Sync health check (for backward compatibility)."""
        return {
            "status": "healthy" if self.supabase else "degraded",
            "name": self.name,
        }

    # ═══════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════

    def start(self):
        self._running = True
        logger.info("GrowthbridgePlugin: started")

    def shutdown(self):
        self._running = False
        logger.info("GrowthbridgePlugin: stopped")

    # ═══════════════════════════════════════════════════════
    # COMMANDS
    # ═══════════════════════════════════════════════════════

    def get_commands(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "growthbridge_stats",
                "intent": "growthbridge_stats",
                "description": "Get Growthbridge platform statistics",
                "handler": self._handle_stats,
            }
        ]

    def get_webhooks(self) -> List[Dict[str, Any]]:
        return []

    def _handle_stats(self, params: dict) -> dict:
        if not self.supabase:
            return {"ok": False, "error": "Database not available"}

        stats = {}
        tables = [
            "growthbridge_services", "growthbridge_projects", "growthbridge_talent",
            "growthbridge_applications", "growthbridge_partners", "growthbridge_blog",
        ]
        
        for table in tables:
            try:
                result = self.supabase.table(table).select("id", count="exact").execute()
                stats[table.replace("growthbridge_", "")] = (
                    result.count if hasattr(result, 'count') else len(result.data or [])
                )
            except Exception:
                stats[table.replace("growthbridge_", "")] = 0

        return {"ok": True, "stats": stats}
