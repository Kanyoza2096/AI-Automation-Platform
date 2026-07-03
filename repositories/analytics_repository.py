from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from repositories.base import Repository

logger = logging.getLogger(__name__)


class AnalyticsRepository(Repository[dict]):
    """Persists analytics events (posts published, messages handled, etc.)."""

    TABLE = "analytics_events"

    def __init__(self) -> None:
        self._buffer: list[dict] = []
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from services.supabase import get_service_client_from_settings
                self._client = get_service_client_from_settings()
            except Exception:
                pass
        return self._client

    def find_by_id(self, record_id: str) -> Optional[dict]:
        client = self._get_client()
        if client is None:
            return None
        result = self._safe_call(
            lambda: client.table(self.TABLE).select("*").eq("id", record_id).maybe_single().execute()
        )
        return result.data if result else None

    def save(self, entity: dict) -> dict:
        entity.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
        self._buffer.append(entity)

        client = self._get_client()
        if client is None:
            return entity

        self._safe_call(lambda: client.table(self.TABLE).insert(entity).execute())
        return entity

    def delete(self, record_id: str) -> bool:
        client = self._get_client()
        if client is None:
            return False
        result = self._safe_call(
            lambda: client.table(self.TABLE).delete().eq("id", record_id).execute()
        )
        return result is not None

    def record_post_published(self, topic: str, platform: str, post_id: str) -> None:
        self.save({
            "event": "post_published",
            "topic": topic,
            "platform": platform,
            "post_id": post_id,
        })

    def record_message(self, sender_id: str, direction: str) -> None:
        self.save({
            "event": "message",
            "sender_id": sender_id,
            "direction": direction,
        })

    def get_recent(self, limit: int = 50) -> list[dict]:
        client = self._get_client()
        if client is None:
            return list(reversed(self._buffer[-limit:]))
        result = self._safe_call(
            lambda: client.table(self.TABLE)
            .select("*")
            .order("recorded_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data if result and result.data else list(reversed(self._buffer[-limit:]))

    def get_counts(self) -> dict:
        return {
            "buffered_events": len(self._buffer),
            "posts_published": sum(1 for e in self._buffer if e.get("event") == "post_published"),
            "messages_handled": sum(1 for e in self._buffer if e.get("event") == "message"),
        }
