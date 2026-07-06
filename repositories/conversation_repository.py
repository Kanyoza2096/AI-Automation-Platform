from __future__ import annotations

import logging
from typing import Optional

from repositories.base import Repository

logger = logging.getLogger(__name__)


class ConversationRepository(Repository[dict]):
    """Persists conversation turns for context-aware AI replies."""

    TABLE = "conversations"

    def __init__(self) -> None:
        self._cache: dict[str, list[dict]] = {}
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
        turns = self.get_turns(sender_id)
        return {"sender_id": sender_id, "turns": turns} if turns else None

    def get_turns(self, sender_id: str, limit: int = 20) -> list[dict]:
        if sender_id in self._cache:
            return self._cache[sender_id][-limit:]

        client = self._get_client()
        if client is None:
            return []

        result = self._safe_call(
            lambda: client.table(self.TABLE)
            .select("*")
            .eq("sender_id", sender_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        turns = list(reversed(result.data)) if result and result.data else []
        self._cache[sender_id] = turns
        return turns

    def append_turn(self, sender_id: str, role: str, content: str) -> None:
        turn = {"sender_id": sender_id, "role": role, "content": content}
        self._cache.setdefault(sender_id, []).append(turn)

        client = self._get_client()
        if client is None:
            return

        self._safe_call(
            lambda: client.table(self.TABLE).insert(turn).execute()
        )

    def save(self, entity: dict) -> dict:
        sender_id = entity.get("sender_id", "")
        self._cache[sender_id] = entity.get("turns", [])
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

    def clear_turns(self, sender_id: str) -> None:
        self._cache.pop(sender_id, None)
        client = self._get_client()
        if client:
            self._safe_call(
                lambda: client.table(self.TABLE).delete().eq("sender_id", sender_id).execute()
            )
