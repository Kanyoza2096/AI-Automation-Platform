"""
Growthbridge Plugin — Youth Business Ecosystem.
Registered with the Kanyoza AI Platform as an industry plugin.
"""

import logging
from typing import List, Dict, Any

logger = logging.getLogger("kanyoza.plugins.growthbridge")


class GrowthbridgePlugin:
    """Growthbridge business ecosystem plugin.
    
    Provides:
      - Public API: services, projects, blog, testimonials
      - Application intake: talent, partners, contact
      - Admin: CRUD management for all entities
    """

    def __init__(self, supabase_client=None, redis_client=None):
        self.supabase = supabase_client
        self.redis = redis_client
        self.name = "growthbridge"

    def get_commands(self) -> List[Dict[str, Any]]:
        """Register natural language commands."""
        return [
            {
                "name": "growthbridge_stats",
                "description": "Get Growthbridge platform statistics",
                "handler": self._handle_stats,
            }
        ]

    def get_webhooks(self) -> List[Dict[str, Any]]:
        """No webhooks needed — REST API only."""
        return []

    def _handle_stats(self, params: dict) -> dict:
        """Return basic stats about the Growthbridge platform."""
        if not self.supabase:
            return {"ok": False, "error": "Database not available"}

        stats = {}
        tables = ["growthbridge_services", "growthbridge_projects", "growthbridge_talent",
                   "growthbridge_applications", "growthbridge_partners", "growthbridge_blog"]
        
        for table in tables:
            try:
                result = self.supabase.table(table).select("id", count="exact").execute()
                stats[table.replace("growthbridge_", "")] = result.count if hasattr(result, 'count') else len(result.data or [])
            except Exception:
                stats[table.replace("growthbridge_", "")] = 0

        return {"ok": True, "stats": stats}
