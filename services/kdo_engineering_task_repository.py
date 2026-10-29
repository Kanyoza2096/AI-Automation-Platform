"""Durable repository for KDO engineering-task contracts.

SQLite is intended for local development and deterministic tests. Production
adapters must implement the same contract over KDO's configured durable DB,
with database-enforced tenant/workspace scope and compare-and-swap semantics.
This module never invokes agents, tools, providers, Git, or code execution.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from typing import Optional

from services.kdo_engineering_vertical_contracts import (
    EngineeringTaskContract,
    EngineeringTaskStatus,
    transition_engineering_task,
)


class EngineeringTaskNotFound(LookupError):
    pass


class EngineeringTaskConflict(RuntimeError):
    """Idempotency collision or stale revision; caller must reconcile."""


class EngineeringTaskScopeError(PermissionError):
    pass


def _dump(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _encode(task: EngineeringTaskContract) -> dict:
    return {
        "task_id": task.task_id,
        "tenant_id": task.tenant_id,
        "workspace_id": task.workspace_id,
        "objective": task.objective,
        "acceptance_criteria": _dump({"items": list(task.acceptance_criteria)}),
        "idempotency_key": task.idempotency_key,
        "status": task.status.value,
        "revision": task.revision,
        "repository_ref": task.repository_ref,
        "base_revision": task.base_revision,
        "metadata": _dump(dict(task.metadata)),
        "evidence": _dump(dict(task.evidence)),
        "fingerprint": task.fingerprint(),
    }


def _decode(row: sqlite3.Row) -> EngineeringTaskContract:
    criteria = json.loads(row["acceptance_criteria"])["items"]
    return EngineeringTaskContract(
        task_id=row["task_id"], tenant_id=row["tenant_id"],
        workspace_id=row["workspace_id"], objective=row["objective"],
        acceptance_criteria=tuple(criteria), idempotency_key=row["idempotency_key"],
        status=EngineeringTaskStatus(row["status"]), revision=row["revision"],
        repository_ref=row["repository_ref"], base_revision=row["base_revision"],
        metadata=json.loads(row["metadata"]), evidence=json.loads(row["evidence"]),
    )


class SQLiteEngineeringTaskRepository:
    """Transactional local repository with scope-aware reads and revision CAS."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if self.db_path != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA synchronous = FULL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS kdo_engineering_tasks (
                task_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                objective TEXT NOT NULL,
                acceptance_criteria TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK (revision >= 1),
                repository_ref TEXT,
                base_revision TEXT,
                metadata TEXT NOT NULL,
                evidence TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (tenant_id, workspace_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS idx_kdo_eng_tasks_scope_updated
              ON kdo_engineering_tasks(tenant_id, workspace_id, updated_at);
        """)

    def create(self, task: EngineeringTaskContract) -> EngineeringTaskContract:
        row = _encode(task)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                existing = self._db.execute(
                    "SELECT * FROM kdo_engineering_tasks WHERE tenant_id=? AND workspace_id=? AND idempotency_key=?",
                    (task.tenant_id, task.workspace_id, task.idempotency_key),
                ).fetchone()
                if existing:
                    old = _decode(existing)
                    if old.fingerprint() == task.fingerprint():
                        self._db.execute("COMMIT")
                        return old
                    raise EngineeringTaskConflict("idempotency_key_reused_with_different_task")
                self._db.execute("""INSERT INTO kdo_engineering_tasks
                    (task_id,tenant_id,workspace_id,objective,acceptance_criteria,idempotency_key,status,revision,repository_ref,base_revision,metadata,evidence,fingerprint)
                    VALUES (:task_id,:tenant_id,:workspace_id,:objective,:acceptance_criteria,:idempotency_key,:status,:revision,:repository_ref,:base_revision,:metadata,:evidence,:fingerprint)""", row)
                self._db.execute("COMMIT")
                return task
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def get(self, task_id: str, *, tenant_id: str, workspace_id: str) -> EngineeringTaskContract:
        if not tenant_id or not workspace_id:
            raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        with self._lock:
            row = self._db.execute("""SELECT * FROM kdo_engineering_tasks
                WHERE task_id=? AND tenant_id=? AND workspace_id=?""",
                (task_id, tenant_id, workspace_id)).fetchone()
        if row is None:
            # Deliberately avoid revealing whether another tenant owns this ID.
            raise EngineeringTaskNotFound("engineering_task_not_found")
        task = _decode(row)
        if task.fingerprint() != row["fingerprint"]:
            raise EngineeringTaskConflict("stored_task_fingerprint_mismatch")
        return task

    def list_scope(self, *, tenant_id: str, workspace_id: str, limit: int = 100) -> list[EngineeringTaskContract]:
        if not tenant_id or not workspace_id:
            raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        if not 1 <= limit <= 500:
            raise ValueError("limit_must_be_between_1_and_500")
        with self._lock:
            rows = self._db.execute("""SELECT * FROM kdo_engineering_tasks
                WHERE tenant_id=? AND workspace_id=? ORDER BY updated_at DESC, task_id LIMIT ?""",
                (tenant_id, workspace_id, limit)).fetchall()
        tasks = [_decode(row) for row in rows]
        for task, row in zip(tasks, rows):
            if task.fingerprint() != row["fingerprint"]:
                raise EngineeringTaskConflict("stored_task_fingerprint_mismatch")
        return tasks

    def list_status(self, status: EngineeringTaskStatus, *, tenant_id: str, workspace_id: str,
                    limit: int = 100) -> list[EngineeringTaskContract]:
        """List tasks in one lifecycle state within an explicit scope.

        Recovery callers must independently establish worker liveness before
        changing a RUNNING task; this query does not imply that a task is stale.
        """
        if not tenant_id or not workspace_id:
            raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        if not 1 <= limit <= 500:
            raise ValueError("limit_must_be_between_1_and_500")
        with self._lock:
            rows = self._db.execute("""SELECT * FROM kdo_engineering_tasks
                WHERE tenant_id=? AND workspace_id=? AND status=?
                ORDER BY updated_at ASC, task_id LIMIT ?""",
                (tenant_id, workspace_id, status.value, limit)).fetchall()
        tasks = [_decode(row) for row in rows]
        for task, row in zip(tasks, rows):
            if task.fingerprint() != row["fingerprint"]:
                raise EngineeringTaskConflict("stored_task_fingerprint_mismatch")
        return tasks

    def transition(self, task_id: str, next_status: EngineeringTaskStatus, *,
                   tenant_id: str, workspace_id: str, expected_revision: int,
                   evidence: Optional[dict] = None) -> EngineeringTaskContract:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("""SELECT * FROM kdo_engineering_tasks
                    WHERE task_id=? AND tenant_id=? AND workspace_id=?""",
                    (task_id, tenant_id, workspace_id)).fetchone()
                if row is None:
                    raise EngineeringTaskNotFound("engineering_task_not_found")
                current = _decode(row)
                if current.fingerprint() != row["fingerprint"]:
                    raise EngineeringTaskConflict("stored_task_fingerprint_mismatch")
                if current.revision != expected_revision:
                    raise EngineeringTaskConflict("stale_revision")
                updated = transition_engineering_task(
                    current, next_status, evidence=evidence,
                    tenant_id=tenant_id, workspace_id=workspace_id)
                encoded = _encode(updated)
                result = self._db.execute("""UPDATE kdo_engineering_tasks SET
                    status=?,revision=?,evidence=?,fingerprint=?,updated_at=CURRENT_TIMESTAMP
                    WHERE task_id=? AND tenant_id=? AND workspace_id=? AND revision=?""",
                    (encoded["status"], encoded["revision"], encoded["evidence"], encoded["fingerprint"],
                     task_id, tenant_id, workspace_id, expected_revision))
                if result.rowcount != 1:
                    raise EngineeringTaskConflict("compare_and_swap_failed")
                self._db.execute("COMMIT")
                return updated
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def update_evidence(self, task_id: str, *, tenant_id: str, workspace_id: str,
                        expected_revision: int, evidence: dict) -> EngineeringTaskContract:
        """Merge auditable evidence with revision CAS without changing lifecycle state."""
        if not tenant_id or not workspace_id:
            raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        if not isinstance(evidence, dict):
            raise ValueError("evidence_must_be_dict")
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT * FROM kdo_engineering_tasks WHERE task_id=? AND tenant_id=? AND workspace_id=?",
                    (task_id, tenant_id, workspace_id)).fetchone()
                if row is None:
                    raise EngineeringTaskNotFound("engineering_task_not_found")
                current = _decode(row)
                if current.fingerprint() != row["fingerprint"]:
                    raise EngineeringTaskConflict("stored_task_fingerprint_mismatch")
                if current.revision != expected_revision:
                    raise EngineeringTaskConflict("stale_revision")
                merged = dict(current.evidence)
                merged.update(evidence)
                updated = replace(current, evidence=merged, revision=current.revision + 1)
                encoded = _encode(updated)
                result = self._db.execute("UPDATE kdo_engineering_tasks SET revision=?,evidence=?,fingerprint=?,updated_at=CURRENT_TIMESTAMP WHERE task_id=? AND tenant_id=? AND workspace_id=? AND revision=?",
                    (encoded["revision"], encoded["evidence"], encoded["fingerprint"], task_id, tenant_id, workspace_id, expected_revision))
                if result.rowcount != 1:
                    raise EngineeringTaskConflict("compare_and_swap_failed")
                self._db.execute("COMMIT")
                return updated
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def close(self) -> None:
        with self._lock:
            self._db.close()
