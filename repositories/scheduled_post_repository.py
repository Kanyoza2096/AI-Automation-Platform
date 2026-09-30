from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from repositories.base import Repository

logger = logging.getLogger(__name__)


class ScheduledPostRepository(Repository[dict]):
    """Tracks scheduled posts and their execution history."""

    TABLE = "scheduled_posts"

    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from services.supabase import get_service_client_from_settings
                self._client = get_service_client_from_settings()
            except Exception as e:
                logger.warning("Failed to get Supabase client: %s", e)
        return self._client

    def find_by_id(self, record_id: str) -> Optional[dict]:
        if record_id in self._cache:
            return self._cache[record_id]
        client = self._get_client()
        if client is None:
            return None
        try:
            result = client.table(self.TABLE).select("*").eq("id", record_id).maybe_single().execute()
            if result and result.data:
                self._cache[record_id] = result.data
                return result.data
        except Exception as e:
            logger.error("Failed to find scheduled post %s: %s", record_id, e)
        return None

    def save(self, entity: dict) -> dict:
        record_id = entity.get("id", "")
        entity.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        self._cache[record_id] = entity

        client = self._get_client()
        if client is None:
            return entity
        try:
            client.table(self.TABLE).upsert(entity, on_conflict="id").execute()
            logger.info("Scheduled post saved: %s", record_id)
        except Exception as e:
            logger.error("Failed to save scheduled post %s: %s", record_id, e)
        return entity

    def delete(self, record_id: str) -> bool:
        self._cache.pop(record_id, None)
        client = self._get_client()
        if client is None:
            return True
        try:
            client.table(self.TABLE).delete().eq("id", record_id).execute()
            logger.info("Scheduled post deleted: %s", record_id)
            return True
        except Exception as e:
            logger.error("Failed to delete scheduled post %s: %s", record_id, e)
            return False

    def get_pending(self) -> list[dict]:
        client = self._get_client()
        if client is None:
            return [p for p in self._cache.values() if p.get("status") == "pending"]
        try:
            result = client.table(self.TABLE) \
                .select("*") \
                .eq("status", "pending") \
                .order("scheduled_at") \
                .limit(50) \
                .execute()
            return result.data if result and result.data else []
        except Exception as e:
            logger.error("Failed to get pending scheduled posts: %s", e)
            return [p for p in self._cache.values() if p.get("status") == "pending"]

    def mark_published(self, record_id: str, post_id: str) -> None:
        if record_id in self._cache:
            self._cache[record_id]["status"] = "published"
            self._cache[record_id]["post_id"] = post_id
        client = self._get_client()
        if client:
            try:
                client.table(self.TABLE) \
                    .update({
                        "status": "published",
                        "post_id": post_id,
                        "published_at": datetime.now(timezone.utc).isoformat()
                    }) \
                    .eq("id", record_id) \
                    .execute()
                logger.info("Scheduled post marked published: %s", record_id)
            except Exception as e:
                logger.error("Failed to mark post %s as published: %s", record_id, e)
