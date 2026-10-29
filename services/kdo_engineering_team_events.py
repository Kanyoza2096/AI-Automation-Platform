"""Append-only local event store for agent task coordination.

SQLite is a local/dev adapter only. This event store does not replace the
platform's authoritative execution ledger or constitute signed action receipts.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path


class TeamEventStore:
    def __init__(self, database_path: str | Path = ":memory:"):
        self._db = sqlite3.connect(str(database_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.execute("""CREATE TABLE IF NOT EXISTS kdo_team_events (
                event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                sequence_no INTEGER NOT NULL, event_type TEXT NOT NULL,
                actor_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                created_at REAL NOT NULL, previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                UNIQUE(tenant_id, workspace_id, task_id, sequence_no)
            )""")
            self._db.commit()

    def append(self, *, task_id: str, tenant_id: str, workspace_id: str,
               event_type: str, actor_id: str, payload: dict,
               expected_previous_hash: str | None = None) -> dict:
        if not all(isinstance(x, str) and x.strip() for x in
                   (task_id, tenant_id, workspace_id, event_type, actor_id)):
            raise ValueError("task_scope_event_type_and_actor_required")
        if not isinstance(payload, dict):
            raise ValueError("payload_must_be_object")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                last = self._db.execute("""SELECT sequence_no,event_hash FROM kdo_team_events
                    WHERE tenant_id=? AND workspace_id=? AND task_id=?
                    ORDER BY sequence_no DESC LIMIT 1""",
                    (tenant_id, workspace_id, task_id)).fetchone()
                seq = 1 if last is None else last["sequence_no"] + 1
                previous = "GENESIS" if last is None else last["event_hash"]
                if expected_previous_hash is not None and expected_previous_hash != previous:
                    raise ValueError("event_chain_compare_and_swap_conflict")
                event_id = uuid.uuid4().hex
                created = time.time()
                body = {"event_id": event_id, "task_id": task_id, "tenant_id": tenant_id,
                    "workspace_id": workspace_id, "sequence_no": seq, "event_type": event_type,
                    "actor_id": actor_id, "payload_json": encoded, "created_at": created,
                    "previous_hash": previous}
                digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                self._db.execute("""INSERT INTO kdo_team_events VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (event_id, task_id, tenant_id, workspace_id, seq, event_type, actor_id,
                     encoded, created, previous, digest))
                self._db.commit()
                return {**body, "event_hash": digest, "payload": payload}
            except Exception:
                self._db.rollback()
                raise

    def list_events(self, *, task_id: str, tenant_id: str, workspace_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("""SELECT * FROM kdo_team_events
                WHERE task_id=? AND tenant_id=? AND workspace_id=? ORDER BY sequence_no""",
                (task_id, tenant_id, workspace_id)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def verify_chain(self, *, task_id: str, tenant_id: str, workspace_id: str) -> bool:
        events = self.list_events(task_id=task_id, tenant_id=tenant_id, workspace_id=workspace_id)
        previous = "GENESIS"
        for index, event in enumerate(events, 1):
            if event["sequence_no"] != index or event["previous_hash"] != previous:
                return False
            body = {k: event[k] for k in ("event_id", "task_id", "tenant_id", "workspace_id",
                "sequence_no", "event_type", "actor_id", "payload_json", "created_at", "previous_hash")}
            digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if digest != event["event_hash"]:
                return False
            previous = digest
        return True
