"""Durable workspace-scoped operator notification inbox.

This repository is a read-model/metadata store only. It is not an execution
engine and it never sends notifications to external providers.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional


class NotificationInboxRepository:
    TABLE = "kanyoza_notifications"

    def __init__(self, supabase_client=None):
        if supabase_client is None:
            from services.supabase import supabase_service
            supabase_client = getattr(supabase_service, "service_client", None) or getattr(supabase_service, "client", None)
        self.supabase = supabase_client

    def _require_client(self):
        if self.supabase is None:
            raise RuntimeError("Notification persistence is unavailable")
        return self.supabase

    def list_for_workspace(self, workspace_id: str, *, unread_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        query = self._require_client().table(self.TABLE).select("*").eq("workspace_id", str(workspace_id)).order("created_at", desc=True).limit(limit)
        if unread_only:
            query = query.is_("read_at", "null")
        result = query.execute()
        return result.data or []

    def mark_read(self, workspace_id: str, notification_id: str) -> Optional[dict[str, Any]]:
        result = (
            self._require_client().table(self.TABLE)
            .update({"read_at": datetime.now(timezone.utc).isoformat()})
            .eq("workspace_id", str(workspace_id))
            .eq("id", str(notification_id))
            .is_("read_at", "null")
            .execute()
        )
        return (result.data or [None])[0]

    def create(self, workspace_id: str, *, recipient_id: str, channel: str, body: str,
               subject: Optional[str] = None, priority: str = "normal",
               metadata: Optional[dict[str, Any]] = None, correlation_id: Optional[str] = None) -> dict[str, Any]:
        row = {
            "id": f"notify-{uuid.uuid4().hex[:16]}",
            "workspace_id": str(workspace_id),
            "recipient_id": recipient_id,
            "channel": channel,
            "subject": subject,
            "body": body,
            "priority": priority,
            "metadata": metadata or {},
            "correlation_id": correlation_id,
        }
        result = self._require_client().table(self.TABLE).insert(row).execute()
        return (result.data or [row])[0]
