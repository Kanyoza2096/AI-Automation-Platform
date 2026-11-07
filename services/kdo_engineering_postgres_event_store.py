"""Durable PostgreSQL event adapter for KDO Engineering worker coordination.

Uses the existing Phase 16.9 engineering console event ledger. It is separate
from Action Kernel receipts: events describe workflow activity, while the Action
Kernel remains the authority for consequential actions.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping


class PostgresEngineeringEventStoreError(RuntimeError):
    """Raised when a scoped engineering event cannot be persisted or verified."""


class PostgresEngineeringEventStore:
    def __init__(self, connection_factory: Callable[[], Any]):
        if not callable(connection_factory):
            raise ValueError("postgres_connection_factory_required")
        self._connect = connection_factory

    @staticmethod
    def _scope(task_id: str, tenant_id: str, workspace_id: str,
               event_type: str, actor_id: str, payload: Mapping[str, Any]) -> None:
        values = (task_id, tenant_id, workspace_id, event_type, actor_id)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("complete_event_scope_type_and_actor_required")
        if not isinstance(payload, Mapping):
            raise ValueError("event_payload_must_be_mapping")

    def append(self, *, task_id: str, tenant_id: str, workspace_id: str,
               event_type: str, actor_id: str, payload: Mapping[str, Any],
               expected_previous_hash: str | None = None) -> dict[str, Any]:
        self._scope(task_id, tenant_id, workspace_id, event_type, actor_id, payload)
        payload_json = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str)
        conn = cursor = None
        try:
            conn = self._connect()
            cursor = conn.cursor()
            # Match the console provider's advisory-lock key so web and worker
            # writers serialize on the same task stream.
            stream_key = f"{workspace_id}:{task_id}"
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (stream_key,))
            cursor.execute(
                """SELECT sequence_no,event_hash
                   FROM public.kdo_engineering_console_events
                   WHERE tenant_id=%s AND workspace_id=%s AND task_id=%s
                   ORDER BY sequence_no DESC LIMIT 1 FOR UPDATE""",
                (tenant_id, workspace_id, task_id),
            )
            previous = cursor.fetchone()
            sequence_no = 1 if previous is None else int(previous[0]) + 1
            previous_hash = "GENESIS" if previous is None else str(previous[1])
            if expected_previous_hash is not None and expected_previous_hash != previous_hash:
                raise PostgresEngineeringEventStoreError("event_chain_compare_and_swap_conflict")

            event_id = uuid.uuid4().hex
            created_at = datetime.now(timezone.utc)
            body = {
                "event_id": event_id, "task_id": task_id, "tenant_id": tenant_id,
                "workspace_id": workspace_id, "sequence_no": sequence_no,
                "event_type": event_type, "actor_id": actor_id,
                "payload_json": payload_json, "created_at": created_at.isoformat(),
                "previous_hash": previous_hash,
            }
            event_hash = hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            cursor.execute(
                """INSERT INTO public.kdo_engineering_console_events
                   (event_id,task_id,tenant_id,workspace_id,sequence_no,event_type,actor_id,
                    payload,created_at,previous_hash,event_hash)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)""",
                (event_id, task_id, tenant_id, workspace_id, sequence_no, event_type,
                 actor_id, payload_json, created_at, previous_hash, event_hash),
            )
            conn.commit()
            return {**body, "event_hash": event_hash, "payload": dict(payload)}
        except PostgresEngineeringEventStoreError:
            if conn is not None:
                conn.rollback()
            raise
        except Exception as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise PostgresEngineeringEventStoreError("engineering_event_persistence_failed") from exc
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def list_events(self, *, task_id: str, tenant_id: str, workspace_id: str,
                    after_sequence: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        if any(not isinstance(value, str) or not value.strip()
               for value in (task_id, tenant_id, workspace_id)):
            raise ValueError("complete_event_scope_required")
        conn = cursor = None
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(
                """SELECT event_id,task_id,tenant_id,workspace_id,sequence_no,event_type,
                          actor_id,payload,created_at,previous_hash,event_hash
                   FROM public.kdo_engineering_console_events
                   WHERE tenant_id=%s AND workspace_id=%s AND task_id=%s
                     AND sequence_no>%s
                   ORDER BY sequence_no LIMIT %s""",
                (tenant_id, workspace_id, task_id, max(0, int(after_sequence)),
                 min(max(int(limit), 1), 500)),
            )
            rows = cursor.fetchall()
            events = []
            for row in rows:
                payload = row[7] if isinstance(row[7], Mapping) else json.loads(row[7] or "{}")
                events.append({
                    "event_id": row[0], "task_id": row[1], "tenant_id": row[2],
                    "workspace_id": row[3], "sequence_no": int(row[4]), "event_type": row[5],
                    "actor_id": row[6], "payload": dict(payload),
                    "created_at": row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8]),
                    "previous_hash": row[9], "event_hash": row[10],
                })
            return events
        except Exception as exc:
            raise PostgresEngineeringEventStoreError("engineering_event_read_failed") from exc
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def verify_chain(self, *, task_id: str, tenant_id: str, workspace_id: str) -> bool:
        # Verify bounded pages so the complete stream is checked without
        # loading an unbounded task history into memory.
        previous = "GENESIS"
        expected_sequence = 1
        after_sequence = 0
        page_size = 500
        while True:
            events = self.list_events(
                task_id=task_id, tenant_id=tenant_id, workspace_id=workspace_id,
                after_sequence=after_sequence, limit=page_size,
            )
            if not events:
                return True
            for event in events:
                if (event["sequence_no"] != expected_sequence
                        or event["previous_hash"] != previous):
                    return False
                payload_json = json.dumps(
                    event["payload"], sort_keys=True, separators=(",", ":"), default=str
                )
                body = {
                    "event_id": event["event_id"], "task_id": task_id,
                    "tenant_id": tenant_id, "workspace_id": workspace_id,
                    "sequence_no": expected_sequence, "event_type": event["event_type"],
                    "actor_id": event["actor_id"], "payload_json": payload_json,
                    "created_at": event["created_at"], "previous_hash": previous,
                }
                digest = hashlib.sha256(
                    json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                if digest != event["event_hash"]:
                    return False
                previous = digest
                expected_sequence += 1
                after_sequence = event["sequence_no"]
            if len(events) < page_size:
                return True
