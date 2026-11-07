"""Durable, workspace-scoped recovery for the OrganismRuntime.

The existing events ledger is the observation journal. This adapter stores only
the latest derived state snapshot; it never persists or restores action grants,
leases, approvals, credentials, or execution authority.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from core.organism_state import OperatingMode, OrganismState
from organism.event_bridge import observation_from_event

logger = logging.getLogger("kanyoza.organism.persistence")


class OrganismRuntimeRecoveryError(RuntimeError):
    """Recovery or persistence could not be completed safely."""


class OrganismRuntimeStore:
    SCHEMA_VERSION = 1
    MAX_RECOVERY_EVENTS = 1000

    def __init__(self, client: Any) -> None:
        if client is None:
            raise ValueError("service-role database client is required")
        self.client = client

    async def restore(self, runtime: Any) -> str:
        """Restore one workspace snapshot and its bounded event-derived context."""
        workspace_id = runtime.workspace_id
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("runtime workspace_id is required")

        try:
            snapshot_result, event_result = await asyncio.gather(
                asyncio.to_thread(self._load_snapshot, workspace_id),
                asyncio.to_thread(self._load_events, workspace_id),
            )
            row = (snapshot_result.data or [None])[0]
            restored_snapshot = False
            if row is not None:
                if row.get("workspace_id") != workspace_id:
                    raise OrganismRuntimeRecoveryError("snapshot_workspace_mismatch")
                if int(row.get("schema_version", 0)) != self.SCHEMA_VERSION:
                    raise OrganismRuntimeRecoveryError("incompatible_snapshot_schema")
                state_data = row.get("state")
                if not isinstance(state_data, dict):
                    raise OrganismRuntimeRecoveryError("invalid_snapshot_state")
                encoded = json.dumps(state_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                actual_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
                expected_hash = str(row.get("state_sha256") or "")
                if not hmac.compare_digest(actual_hash, expected_hash):
                    raise OrganismRuntimeRecoveryError("snapshot_integrity_mismatch")
                state = OrganismState.from_mapping(state_data)
                if state.workspace_id != workspace_id:
                    raise OrganismRuntimeRecoveryError("snapshot_workspace_mismatch")
                # A snapshot is cognitive context, never an execution grant.
                state_data = state.to_dict()
                state_data["authority"] = "suggest"
                if state.mode is OperatingMode.EXECUTING:
                    state_data["mode"] = OperatingMode.OBSERVING.value
                runtime.state = OrganismState.from_mapping(state_data)
                restored_snapshot = True

            restored_observations = 0
            rows = list(event_result.data or [])
            # The database query is newest-first for a bounded page. Replay in
            # chronological order so the in-memory buffer retains the newest items.
            for event_row in reversed(rows):
                payload = event_row.get("payload")
                # Ledger rows are persisted input. Malformed JSON shapes must
                # not abort recovery for the entire workspace.
                safe_payload = payload if isinstance(payload, Mapping) else {}
                event = {
                    "event_name": event_row.get("event_type"),
                    "event_id": event_row.get("id"),
                    "workspace_id": event_row.get("workspace_id"),
                    "payload": safe_payload,
                    "source": "durable_event_ledger",
                    "correlation_id": safe_payload.get("correlation_id"),
                    "observed_at": event_row.get("created_at"),
                }
                observation = observation_from_event(event)
                if observation is None:
                    continue
                runtime.ingest(observation)
                restored_observations += 1

            status = "restored" if restored_snapshot or restored_observations else "empty_initial_state"
            runtime.recovery_status = status
            logger.info(
                "Organism runtime recovery complete workspace=%s status=%s observations=%d snapshot=%s",
                workspace_id, status, restored_observations, restored_snapshot,
            )
            return status
        except Exception as exc:
            runtime.recovery_status = "recovery_failed"
            if isinstance(exc, OrganismRuntimeRecoveryError):
                raise
            raise OrganismRuntimeRecoveryError("organism runtime recovery failed") from exc

    async def persist(self, runtime: Any) -> int:
        """Persist the latest derived state; never persist action authority."""
        state = runtime.state
        if state is None:
            return 0
        if state.workspace_id != runtime.workspace_id:
            raise ValueError("state workspace_id does not match runtime")
        data = state.to_dict()
        data["authority"] = "suggest"
        if data.get("mode") == OperatingMode.EXECUTING.value:
            data["mode"] = OperatingMode.OBSERVING.value
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        record = {
            "workspace_id": runtime.workspace_id,
            "schema_version": self.SCHEMA_VERSION,
            "state": data,
            "state_sha256": digest,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            result = await asyncio.to_thread(
                lambda: self.client.table("organism_runtime_snapshots")
                .upsert(record, on_conflict="workspace_id")
                .execute()
            )
            # A successful PostgREST request is the persistence acknowledgement.
            return 1
        except Exception as exc:
            raise OrganismRuntimeRecoveryError("organism runtime snapshot persistence failed") from exc

    def _load_snapshot(self, workspace_id: str):
        return (
            self.client.table("organism_runtime_snapshots")
            .select("workspace_id,schema_version,state,state_sha256,updated_at")
            .eq("workspace_id", workspace_id)
            .limit(1)
            .execute()
        )

    def _load_events(self, workspace_id: str):
        from organism.event_bridge import OrganismEventBridge
        return (
            self.client.table("events")
            .select("id,event_type,payload,workspace_id,created_at")
            .eq("workspace_id", workspace_id)
            .in_("event_type", list(OrganismEventBridge.EVENT_KINDS))
            .order("created_at", desc=True)
            .limit(self.MAX_RECOVERY_EVENTS)
            .execute()
        )
