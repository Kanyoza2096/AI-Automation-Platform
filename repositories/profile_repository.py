from __future__ import annotations

import logging
from typing import Any, Optional

from repositories.base import Repository

logger = logging.getLogger(__name__)


class ProfileRepository(Repository[dict]):
    """Stores and retrieves user memory profiles via Supabase.

    Falls back to an in-memory dict when Supabase is unavailable so the
    app degrades gracefully rather than crashing.
    """

    TABLE = "user_profiles"

    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from services.supabase import get_service_client_from_settings
                self._client = get_service_client_from_settings()
            except Exception:
                pass
        return self._client

    def find_by_id(self, sender_id: str) -> Optional[dict]:
        cached = self._cache.get(sender_id)
        if cached:
            return cached

        client = self._get_client()
        if client is None:
            return None

        result = self._safe_call(
            lambda: client.table(self.TABLE)
            .select("*")
            .eq("sender_id", sender_id)
            .maybe_single()
            .execute()
        )
        if result and result.data:
            self._cache[sender_id] = result.data
            return result.data
        return None

    def save(self, entity: dict) -> dict:
        sender_id = entity.get("sender_id", "")
        self._cache[sender_id] = entity

        client = self._get_client()
        if client is None:
            return entity

        self._safe_call(
            lambda: client.table(self.TABLE)
            .upsert(entity, on_conflict="sender_id")
            .execute()
        )
        return entity

    def delete(self, sender_id: str) -> bool:
        self._cache.pop(sender_id, None)
        client = self._get_client()
        if client is None:
            return True
        result = self._safe_call(
            lambda: client.table(self.TABLE).delete().eq("sender_id", sender_id).execute()
        )
        return result is not None

    def get_all(self, limit: int = 100) -> list[dict]:
        client = self._get_client()
        if client is None:
            return list(self._cache.values())
        result = self._safe_call(
            lambda: client.table(self.TABLE).select("*").limit(limit).execute(),
            default=None,
        )
        return result.data if result and result.data else list(self._cache.values())
