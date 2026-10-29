from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

@dataclass
class ApplicantNotification:
    id: str
    workspace_id: str
    permit_case_id: str
    applicant_ref: str
    to_number: str
    body: str
    idempotency_key: str
    provider_message_sid: str | None = None
    provider_status: str = "pending"
    status: str = "pending"
    reconciliation_required: bool = True
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class GovernmentApplicantNotificationRepository:
    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._notifications: dict[str, ApplicantNotification] = {}
        self._evidence: dict[str, list[dict[str, Any]]] = {}

    def create(self, workspace_id: str, data: dict[str, Any]) -> ApplicantNotification:
        obj = ApplicantNotification(id=f"gov-notify-{uuid.uuid4().hex[:12]}", workspace_id=str(workspace_id), **data)
        self._notifications[obj.id] = obj
        return obj

    def get(self, workspace_id: str, notification_id: str):
        obj = self._notifications.get(str(notification_id))
        return obj if obj and obj.workspace_id == str(workspace_id) else None

    def get_by_idempotency_key(self, workspace_id: str, key: str):
        return next((x for x in self._notifications.values() if x.workspace_id == str(workspace_id) and x.idempotency_key == key), None)

    def update(self, workspace_id: str, notification_id: str, **changes):
        obj = self.get(workspace_id, notification_id)
        if obj is None: raise ValueError("applicant notification not found in workspace")
        for k, v in changes.items():
            if hasattr(obj, k): setattr(obj, k, v)
        obj.updated_at = datetime.now(timezone.utc).isoformat()
        return obj

    def record_evidence(self, workspace_id: str, data: dict[str, Any]):
        self._evidence.setdefault(str(data["notification_id"]), []).insert(0, {**data, "workspace_id": str(workspace_id), "recorded_at": datetime.now(timezone.utc).isoformat()})

    def list_evidence(self, workspace_id: str, notification_id: str, limit: int = 100):
        return [x for x in self._evidence.get(str(notification_id), []) if x.get("workspace_id") == str(workspace_id)][:limit]

    def list_reconciliation_required(self, workspace_id=None, limit: int = 100):
        return [x for x in self._notifications.values() if x.reconciliation_required and (workspace_id is None or x.workspace_id == str(workspace_id))][:limit]
