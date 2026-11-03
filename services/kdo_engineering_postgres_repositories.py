"""Shared PostgreSQL repositories for governed KDO engineering tasks and workspaces.

Connection factories are injected by the host. Schema changes are migration-managed;
this module never discovers credentials or falls back to local SQLite.
"""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Callable

from services.kdo_engineering_task_repository import (
    EngineeringTaskConflict, EngineeringTaskNotFound, EngineeringTaskScopeError,
    _encode, _decode,
)
from services.kdo_engineering_vertical_contracts import (
    EngineeringTaskContract, EngineeringTaskStatus, transition_engineering_task,
)
from services.kdo_engineering_phase16_2 import (
    Phase162Error, WorkspaceRecord,
)


class PostgresEngineeringRepositoryError(RuntimeError):
    """PostgreSQL persistence operation failed."""


def _row_dict(cursor: Any, row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict) or hasattr(row, "keys"):
        return dict(row)
    names = [getattr(col, "name", None) or col[0] for col in (cursor.description or ())]
    return dict(zip(names, row))


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


class _PostgresRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        if not callable(connection_factory):
            raise PostgresEngineeringRepositoryError("postgres_connection_factory_required")
        self._connection_factory = connection_factory

    def _run(self, operation: Callable[[Any, Any], Any], *, write: bool = False) -> Any:
        connection = cursor = None
        try:
            connection = self._connection_factory()
            cursor = connection.cursor()
            result = operation(connection, cursor)
            if write:
                connection.commit()
            return result
        except (EngineeringTaskNotFound, EngineeringTaskConflict, EngineeringTaskScopeError, Phase162Error):
            if connection is not None:
                connection.rollback()
            raise
        except Exception as exc:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            raise PostgresEngineeringRepositoryError("postgres_engineering_repository_operation_failed") from exc
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


class PostgresEngineeringTaskRepository(_PostgresRepository):
    """Scope-aware task persistence with fingerprint checks and revision CAS."""

    TABLE = "public.kdo_engineering_tasks"

    @staticmethod
    def _task(row: dict[str, Any] | None) -> EngineeringTaskContract:
        if row is None:
            raise EngineeringTaskNotFound("engineering_task_not_found")
        row = dict(row)
        for key in ("acceptance_criteria", "metadata", "evidence"):
            row[key] = json.dumps(_json_value(row[key]), sort_keys=True, separators=(",", ":"))
        # Reuse the canonical SQLite decoder after normalizing the row shape.
        class Row(dict):
            def __getitem__(self, key: str) -> Any:
                return super().__getitem__(key)
        task = _decode(Row(row))
        if task.fingerprint() != row["fingerprint"]:
            raise EngineeringTaskConflict("stored_task_fingerprint_mismatch")
        return task

    def create(self, task: EngineeringTaskContract) -> EngineeringTaskContract:
        row = _encode(task)
        def op(_conn: Any, cur: Any) -> EngineeringTaskContract:
            cur.execute(f"""INSERT INTO {self.TABLE}
                (task_id,tenant_id,workspace_id,objective,acceptance_criteria,idempotency_key,status,
                 revision,repository_ref,base_revision,metadata,evidence,fingerprint)
                VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                ON CONFLICT (tenant_id,workspace_id,idempotency_key) DO NOTHING
                RETURNING task_id""",
                (row["task_id"], row["tenant_id"], row["workspace_id"], row["objective"],
                 row["acceptance_criteria"], row["idempotency_key"], row["status"], row["revision"],
                 row["repository_ref"], row["base_revision"], row["metadata"], row["evidence"], row["fingerprint"]))
            inserted = cur.fetchone()
            if inserted is not None:
                return task
            cur.execute(f"SELECT * FROM {self.TABLE} WHERE tenant_id=%s AND workspace_id=%s AND idempotency_key=%s",
                        (task.tenant_id, task.workspace_id, task.idempotency_key))
            existing_row = _row_dict(cur, cur.fetchone())
            if existing_row is None:
                raise EngineeringTaskConflict("task_id_already_exists")
            existing = self._task(existing_row)
            if existing.fingerprint() == task.fingerprint():
                return existing
            raise EngineeringTaskConflict("idempotency_key_reused_with_different_task")
        return self._run(op, write=True)

    def get(self, task_id: str, *, tenant_id: str, workspace_id: str) -> EngineeringTaskContract:
        if not tenant_id or not workspace_id:
            raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        def op(_conn: Any, cur: Any) -> EngineeringTaskContract:
            cur.execute(f"SELECT * FROM {self.TABLE} WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s",
                        (task_id, tenant_id, workspace_id))
            row = _row_dict(cur, cur.fetchone())
            if row is None:
                raise EngineeringTaskNotFound("engineering_task_not_found")
            return self._task(row)
        return self._run(op)

    def _list(self, clause: str, params: tuple[Any, ...], limit: int) -> list[EngineeringTaskContract]:
        if not 1 <= limit <= 500:
            raise ValueError("limit_must_be_between_1_and_500")
        def op(_conn: Any, cur: Any) -> list[EngineeringTaskContract]:
            cur.execute(f"SELECT * FROM {self.TABLE} WHERE {clause} ORDER BY updated_at DESC, task_id LIMIT %s", (*params, limit))
            rows = [_row_dict(cur, row) for row in cur.fetchall()]
            return [self._task(row) for row in rows]
        return self._run(op)

    def list_scope(self, *, tenant_id: str, workspace_id: str, limit: int = 100) -> list[EngineeringTaskContract]:
        if not tenant_id or not workspace_id:
            raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        return self._list("tenant_id=%s AND workspace_id=%s", (tenant_id, workspace_id), limit)

    def list_status(self, status: EngineeringTaskStatus, *, tenant_id: str, workspace_id: str,
                    limit: int = 100) -> list[EngineeringTaskContract]:
        if not tenant_id or not workspace_id:
            raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        return self._list("tenant_id=%s AND workspace_id=%s AND status=%s",
                          (tenant_id, workspace_id, status.value), limit)

    def _mutate_task(self, task_id: str, *, tenant_id: str, workspace_id: str,
                     expected_revision: int, evidence: dict, next_status: EngineeringTaskStatus | None) -> EngineeringTaskContract:
        if not tenant_id or not workspace_id:
            raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        if not isinstance(evidence, dict):
            raise ValueError("evidence_must_be_dict")
        def op(_conn: Any, cur: Any) -> EngineeringTaskContract:
            cur.execute(f"SELECT * FROM {self.TABLE} WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s FOR UPDATE",
                        (task_id, tenant_id, workspace_id))
            row = _row_dict(cur, cur.fetchone())
            if row is None:
                raise EngineeringTaskNotFound("engineering_task_not_found")
            current = self._task(row)
            if current.revision != expected_revision:
                raise EngineeringTaskConflict("stale_revision")
            if next_status is None:
                merged = dict(current.evidence)
                merged.update(evidence)
                updated = replace(current, evidence=merged, revision=current.revision + 1)
            else:
                updated = transition_engineering_task(current, next_status, evidence=evidence,
                                                       tenant_id=tenant_id, workspace_id=workspace_id)
            encoded = _encode(updated)
            cur.execute(f"""UPDATE {self.TABLE} SET status=%s,revision=%s,evidence=%s::jsonb,
                fingerprint=%s,updated_at=now()
                WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s AND revision=%s""",
                (encoded["status"], encoded["revision"], encoded["evidence"], encoded["fingerprint"],
                 task_id, tenant_id, workspace_id, expected_revision))
            if cur.rowcount != 1:
                raise EngineeringTaskConflict("compare_and_swap_failed")
            return updated
        return self._run(op, write=True)

    def transition(self, task_id: str, next_status: EngineeringTaskStatus, *, tenant_id: str,
                   workspace_id: str, expected_revision: int, evidence: dict | None = None) -> EngineeringTaskContract:
        return self._mutate_task(task_id, tenant_id=tenant_id, workspace_id=workspace_id,
                                 expected_revision=expected_revision, evidence=evidence or {}, next_status=next_status)

    def update_evidence(self, task_id: str, *, tenant_id: str, workspace_id: str,
                        expected_revision: int, evidence: dict) -> EngineeringTaskContract:
        return self._mutate_task(task_id, tenant_id=tenant_id, workspace_id=workspace_id,
                                 expected_revision=expected_revision, evidence=evidence, next_status=None)


class PostgresEngineeringWorkspaceRepository(_PostgresRepository):
    """Durable scope-keyed workspace metadata and immutable first plan decision."""

    WORKSPACES = "public.kdo_engineering_workspaces"
    REVIEWS = "public.kdo_engineering_plan_reviews"

    @staticmethod
    def _record(row: dict[str, Any] | None) -> WorkspaceRecord:
        if row is None:
            raise EngineeringTaskNotFound("engineering_workspace_not_found")
        for key in ("updated_at",):
            if hasattr(row[key], "isoformat"):
                row[key] = row[key].isoformat()
        return WorkspaceRecord(**row)

    def put(self, *, tenant_id: str, workspace_id: str, task_id: str, path: str,
            branch: str, base_revision: str, head_revision: str, status: str = "active") -> WorkspaceRecord:
        def op(_conn: Any, cur: Any) -> WorkspaceRecord:
            cur.execute(f"SELECT * FROM {self.WORKSPACES} WHERE tenant_id=%s AND workspace_id=%s AND task_id=%s FOR UPDATE",
                        (tenant_id, workspace_id, task_id))
            old = _row_dict(cur, cur.fetchone())
            if old:
                if old["path"] != path or old["branch"] != branch or old["base_revision"] != base_revision:
                    raise Phase162Error("workspace_metadata_conflict")
                cur.execute(f"""UPDATE {self.WORKSPACES} SET head_revision=%s,status=%s,updated_at=now()
                    WHERE tenant_id=%s AND workspace_id=%s AND task_id=%s""",
                    (head_revision, status, tenant_id, workspace_id, task_id))
            else:
                cur.execute(f"""INSERT INTO {self.WORKSPACES}
                    (tenant_id,workspace_id,task_id,path,branch,base_revision,head_revision,status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (tenant_id, workspace_id, task_id, path, branch, base_revision, head_revision, status))
            cur.execute(f"SELECT * FROM {self.WORKSPACES} WHERE tenant_id=%s AND workspace_id=%s AND task_id=%s",
                        (tenant_id, workspace_id, task_id))
            return self._record(_row_dict(cur, cur.fetchone()))
        return self._run(op, write=True)

    def get(self, task_id: str, *, tenant_id: str, workspace_id: str) -> WorkspaceRecord:
        if not tenant_id or not workspace_id:
            raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        def op(_conn: Any, cur: Any) -> WorkspaceRecord:
            cur.execute(f"SELECT * FROM {self.WORKSPACES} WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s",
                        (task_id, tenant_id, workspace_id))
            return self._record(_row_dict(cur, cur.fetchone()))
        return self._run(op)

    def list_scope(self, *, tenant_id: str, workspace_id: str, limit: int = 100) -> list[WorkspaceRecord]:
        if not tenant_id or not workspace_id:
            raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        if not 1 <= limit <= 500:
            raise ValueError("limit_must_be_between_1_and_500")
        def op(_conn: Any, cur: Any) -> list[WorkspaceRecord]:
            cur.execute(f"SELECT * FROM {self.WORKSPACES} WHERE tenant_id=%s AND workspace_id=%s ORDER BY updated_at,task_id LIMIT %s",
                        (tenant_id, workspace_id, limit))
            return [self._record(_row_dict(cur, row)) for row in cur.fetchall()]
        return self._run(op)

    def mark_status(self, task_id: str, *, tenant_id: str, workspace_id: str, status: str,
                    head_revision: str | None = None) -> WorkspaceRecord:
        def op(_conn: Any, cur: Any) -> WorkspaceRecord:
            if head_revision is None:
                cur.execute(f"UPDATE {self.WORKSPACES} SET status=%s,updated_at=now() WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s",
                            (status, task_id, tenant_id, workspace_id))
            else:
                cur.execute(f"UPDATE {self.WORKSPACES} SET status=%s,head_revision=%s,updated_at=now() WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s",
                            (status, head_revision, task_id, tenant_id, workspace_id))
            if cur.rowcount != 1:
                raise EngineeringTaskNotFound("engineering_workspace_not_found")
            cur.execute(f"SELECT * FROM {self.WORKSPACES} WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s",
                        (task_id, tenant_id, workspace_id))
            return self._record(_row_dict(cur, cur.fetchone()))
        return self._run(op, write=True)

    def record_plan_review(self, *, tenant_id: str, workspace_id: str, task_id: str,
                           plan_digest: str, reviewer_id: str, decision: str, reason: str = "") -> None:
        if decision not in {"approved", "rejected"}:
            raise ValueError("invalid_plan_decision")
        def op(_conn: Any, cur: Any) -> None:
            cur.execute(f"""INSERT INTO {self.REVIEWS}
                (tenant_id,workspace_id,task_id,plan_digest,reviewer_id,decision,reason)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (tenant_id, workspace_id, task_id, plan_digest, reviewer_id, decision, reason[:1000]))
        try:
            self._run(op, write=True)
        except PostgresEngineeringRepositoryError as exc:
            raise Phase162Error("plan_review_already_recorded_or_persistence_failed") from exc

    def get_plan_review(self, task_id: str, *, tenant_id: str, workspace_id: str) -> dict[str, str]:
        if not tenant_id or not workspace_id:
            raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        def op(_conn: Any, cur: Any) -> dict[str, str]:
            cur.execute(f"SELECT * FROM {self.REVIEWS} WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s",
                        (task_id, tenant_id, workspace_id))
            row = _row_dict(cur, cur.fetchone())
            if row is None:
                raise EngineeringTaskNotFound("engineering_plan_review_not_found")
            return {k: (v.isoformat() if hasattr(v, "isoformat") else str(v)) for k, v in row.items()}
        return self._run(op)
