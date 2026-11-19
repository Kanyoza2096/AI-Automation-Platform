"""Supabase persistence boundary for the Director mission runtime.

All mission writes go through versioned, idempotent RPCs. This class never
writes directly to director_missions or director_mission_events.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..utils import safe


class MissionRuntimeStoreError(RuntimeError):
    """Durable mission state could not be read or acknowledged."""


@dataclass(frozen=True)
class MissionEventWrite:
    scope_type: str
    workspace_id: str | None
    mission_id: str
    expected_version: int
    idempotency_key: str
    request_fingerprint: str
    event_type: str
    actor_type: str
    actor_id: str
    correlation_id: str
    payload: Mapping[str, Any]
    state_snapshot: Mapping[str, Any]
    evidence_refs: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    recipient_type: str | None = None
    recipient_id: str | None = None
    task_id: str | None = None
    message_id: str | None = None
    thread_id: str | None = None
    round_number: int = 0
    status: str | None = None
    phase: str | None = None
    task_sync: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.scope_type not in {"workspace", "platform"}:
            raise ValueError("invalid_mission_scope_type")
        if self.scope_type == "workspace" and not str(self.workspace_id or "").strip():
            raise ValueError("workspace_mission_requires_workspace_id")
        if self.scope_type == "platform" and self.workspace_id is not None:
            raise ValueError("platform_mission_cannot_have_workspace_id")
        for name in ("mission_id", "idempotency_key", "request_fingerprint",
                     "event_type", "actor_type", "actor_id", "correlation_id"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name}_required")
        if len(self.request_fingerprint) != 64:
            raise ValueError("request_fingerprint_must_be_sha256_hex")
        if self.expected_version < 0 or self.round_number < 0:
            raise ValueError("mission_version_and_round_must_be_nonnegative")
        if not isinstance(self.payload, Mapping) or not isinstance(self.state_snapshot, Mapping):
            raise TypeError("mission_event_payload_and_snapshot_must_be_mappings")
        if self.recipient_type is None and self.recipient_id is not None:
            raise ValueError("recipient_id_requires_recipient_type")
        if self.recipient_type is not None and not str(self.recipient_id or "").strip():
            raise ValueError("recipient_type_requires_recipient_id")


class MissionRuntimeStore:
    """Durable mission state/event access layered on Director's pooled store."""

    MAX_EVENT_READ_LIMIT = 500

    def __init__(self, director_store: Any) -> None:
        if not hasattr(director_store, "supabase") or not hasattr(director_store, "_execute_with_retry"):
            raise TypeError("MissionRuntimeStore_requires_AsyncSupabaseStore")
        self._store = director_store

    async def _execute(self, operation: str, fn):
        result = await self._store._execute_with_retry(operation, fn)
        if result is None:
            raise MissionRuntimeStoreError(f"mission_store_unavailable:{operation}")
        return result

    @staticmethod
    def _payload(response: Any) -> dict[str, Any] | None:
        payload = getattr(response, "data", None)
        if isinstance(payload, list) and payload:
            payload = payload[0]
        return payload if isinstance(payload, dict) else None

    async def admit_workspace_mission(
        self,
        *,
        workspace_id: str,
        idempotency_key: str,
        plan_id: str,
        mission_id: str,
        request_fingerprint: str,
        tasks: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any] | None:
        """Admit tasks and create the durable mission aggregate/event atomically."""
        if not workspace_id or not idempotency_key or not plan_id or not mission_id:
            raise ValueError("workspace_mission_admission_identity_required")
        if len(request_fingerprint) != 64:
            raise ValueError("request_fingerprint_must_be_sha256_hex")
        params = {
            "p_workspace_id": workspace_id,
            "p_idempotency_key": idempotency_key,
            "p_plan_id": plan_id,
            "p_mission_id": mission_id,
            "p_request_fingerprint": request_fingerprint,
            "p_tasks": safe(list(tasks)),
        }
        response = await self._execute(
            "stage Director mission v2",
            lambda: self._store.supabase.rpc("stage_director_mission_v2", params).execute(),
        )
        return self._payload(response)

    async def append_event(self, event: MissionEventWrite) -> dict[str, Any] | None:
        """Append one durable event and atomically advance mission version/state."""
        params = {
            "p_scope_type": event.scope_type,
            "p_workspace_id": event.workspace_id,
            "p_mission_id": event.mission_id,
            "p_expected_version": event.expected_version,
            "p_idempotency_key": event.idempotency_key,
            "p_request_fingerprint": event.request_fingerprint,
            "p_event_type": event.event_type,
            "p_actor_type": event.actor_type,
            "p_actor_id": event.actor_id,
            "p_recipient_type": event.recipient_type,
            "p_recipient_id": event.recipient_id,
            "p_task_id": event.task_id,
            "p_message_id": event.message_id,
            "p_thread_id": event.thread_id,
            "p_round_number": event.round_number,
            "p_correlation_id": event.correlation_id,
            "p_payload": safe(dict(event.payload)),
            "p_evidence_refs": safe(list(event.evidence_refs)),
            "p_status": event.status,
            "p_phase": event.phase,
            "p_state_snapshot": safe(dict(event.state_snapshot)),
        }
        response = await self._execute(
            "append Director mission event with atomic task sync",
            lambda: self._store.supabase.rpc(
                "append_director_mission_event_with_task_sync",
                {"p_event": params, "p_task_sync": safe(dict(event.task_sync)) if event.task_sync else None},
            ).execute(),
        )
        return self._payload(response)

    async def get_mission(
        self, *, mission_id: str, scope_type: str, workspace_id: str | None = None
    ) -> dict[str, Any] | None:
        query = self._store.supabase.table("director_missions").select("*")
        query = query.eq("mission_id", mission_id).eq("scope_type", scope_type)
        if scope_type == "workspace":
            if not workspace_id:
                raise ValueError("workspace_id_required_for_mission_read")
            query = query.eq("workspace_id", workspace_id)
        elif scope_type == "platform":
            if workspace_id is not None:
                raise ValueError("platform_mission_read_cannot_include_workspace_id")
            query = query.is_("workspace_id", "null")
        else:
            raise ValueError("invalid_mission_scope_type")
        response = await self._execute(
            "read Director mission aggregate",
            lambda: query.limit(1).execute(),
        )
        rows = getattr(response, "data", None) or []
        return dict(rows[0]) if rows else None

    async def get_workspace_task(
        self, *, task_id: str, workspace_id: str, mission_id: str
    ) -> dict[str, Any] | None:
        """Read a canonical task only when its workspace and mission match."""
        query = (
            self._store.supabase.table("director_tasks").select("*")
            .eq("id", task_id)
            .eq("workspace_id", workspace_id)
            .eq("scope_type", "workspace")
        )
        response = await self._execute(
            "read canonical Director workspace task",
            lambda: query.limit(1).execute(),
        )
        rows = getattr(response, "data", None) or []
        if not rows:
            return None
        row = dict(rows[0])
        payload = row.get("payload") or {}
        if isinstance(payload, str):
            import json
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                return None
        if not isinstance(payload, dict):
            return None
        if (
            row.get("workspace_id") != workspace_id
            or row.get("scope_type") != "workspace"
            or payload.get("workspace_id") != workspace_id
            or payload.get("scope_type") != "workspace"
            or payload.get("mission_id") != mission_id
            or payload.get("status") != row.get("status")
        ):
            return None
        row["payload"] = payload
        return row

    async def find_event(
        self,
        *,
        mission_id: str,
        scope_type: str,
        workspace_id: str | None,
        idempotency_key: str,
        event_type: str,
        task_id: str,
    ) -> dict[str, Any] | None:
        """Find one durable event by its scoped idempotency identity."""
        if not all(str(value or "").strip() for value in (mission_id, idempotency_key, event_type, task_id)):
            raise ValueError("mission_event_lookup_identity_required")
        query = self._store.supabase.table("director_mission_events").select("*")
        query = (
            query.eq("mission_id", mission_id)
            .eq("scope_type", scope_type)
            .eq("idempotency_key", idempotency_key)
            .eq("event_type", event_type)
            .eq("task_id", task_id)
        )
        if scope_type == "workspace":
            if not workspace_id:
                raise ValueError("workspace_id_required_for_mission_event_lookup")
            query = query.eq("workspace_id", workspace_id)
        elif scope_type == "platform":
            if workspace_id is not None:
                raise ValueError("platform_mission_event_lookup_cannot_include_workspace_id")
            query = query.is_("workspace_id", "null")
        else:
            raise ValueError("invalid_mission_scope_type")
        response = await self._execute(
            "find Director mission event by idempotency identity",
            lambda: query.limit(1).execute(),
        )
        rows = getattr(response, "data", None) or []
        return dict(rows[0]) if rows else None

    async def list_events(
        self,
        *,
        mission_id: str,
        scope_type: str,
        workspace_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not 1 <= int(limit) <= self.MAX_EVENT_READ_LIMIT:
            raise ValueError("mission_event_read_limit_out_of_range")
        query = self._store.supabase.table("director_mission_events").select("*")
        query = query.eq("mission_id", mission_id).eq("scope_type", scope_type)
        if scope_type == "workspace":
            if not workspace_id:
                raise ValueError("workspace_id_required_for_mission_event_read")
            query = query.eq("workspace_id", workspace_id)
        elif scope_type == "platform":
            if workspace_id is not None:
                raise ValueError("platform_mission_event_read_cannot_include_workspace_id")
            query = query.is_("workspace_id", "null")
        else:
            raise ValueError("invalid_mission_scope_type")
        response = await self._execute(
            "read Director mission event timeline",
            lambda: query.order("sequence_number", desc=False).limit(int(limit)).execute(),
        )
        return [dict(row) for row in (getattr(response, "data", None) or [])]
