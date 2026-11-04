"""Phase 16.5 PostgreSQL persistence adapters for KDO engineering task/workspace metadata.

These adapters share the canonical PostgreSQL database with Phase 16.4. They are
explicitly injected, server-side DB-API adapters; they never fall back to SQLite
or create schema at runtime. They do not grant execution/release authority.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Callable, Mapping

from services.kdo_engineering_task_repository import (
    EngineeringTaskConflict, EngineeringTaskNotFound, EngineeringTaskScopeError,
    _decode, _dump, _encode,
)
from services.kdo_engineering_vertical_contracts import (
    EngineeringTaskContract, EngineeringTaskStatus, transition_engineering_task,
)
from services.kdo_engineering_phase16_2 import WorkspaceRecord, Phase162Error, _plan_digest
from services.kdo_engineering_phase16_4 import _row_dict


class Phase165Error(RuntimeError):
    """PostgreSQL metadata adapter configuration/persistence failure."""


class _PostgresAdapter:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        if not callable(connection_factory):
            raise Phase165Error("postgres_connection_factory_required")
        self._connection_factory = connection_factory

    def _run(self, operation: Callable[[Any, Any], Any], *, write: bool = False) -> Any:
        conn = cur = None
        try:
            conn = self._connection_factory()
            cur = conn.cursor()
            value = operation(conn, cur)
            if write:
                conn.commit()
            return value
        except (EngineeringTaskNotFound, EngineeringTaskConflict, EngineeringTaskScopeError,
                Phase162Error, ValueError):
            if conn is not None:
                conn.rollback()
            raise
        except Exception as exc:
            if conn is not None:
                try: conn.rollback()
                except Exception: pass
            raise Phase165Error("postgres_engineering_metadata_operation_failed") from exc
        finally:
            if cur is not None:
                try: cur.close()
                except Exception: pass
            if conn is not None:
                try: conn.close()
                except Exception: pass

    @staticmethod
    def _dict(cur: Any, row: Any) -> dict[str, Any] | None:
        return _row_dict(cur, row)


class PostgresEngineeringTaskRepository(_PostgresAdapter):
    """Task contract repository with scoped reads, idempotent create and revision CAS."""
    TABLE = "public.kdo_engineering_tasks"

    @staticmethod
    def _task(row: dict[str, Any] | None) -> EngineeringTaskContract | None:
        if row is None: return None
        # JSONB values arrive as Python values from psycopg; normalize for shared decoder.
        for key in ("acceptance_criteria", "metadata", "evidence"):
            if not isinstance(row.get(key), str):
                value = row.get(key)
                if key == "acceptance_criteria":
                    normalized = value if isinstance(value, dict) and "items" in value else {"items": value or []}
                    row[key] = json.dumps(normalized, separators=(",", ":"))
                else:
                    row[key] = json.dumps(value or {}, separators=(",", ":"), default=str)
        task = _decode(row)
        if task.fingerprint() != row["fingerprint"]:
            raise EngineeringTaskConflict("stored_task_fingerprint_mismatch")
        return task

    def create(self, task: EngineeringTaskContract) -> EngineeringTaskContract:
        data = _encode(task)
        def op(_conn: Any, cur: Any) -> EngineeringTaskContract:
            cur.execute(f"""INSERT INTO {self.TABLE}
                (task_id,tenant_id,workspace_id,objective,acceptance_criteria,idempotency_key,status,revision,
                 repository_ref,base_revision,metadata,evidence,fingerprint)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                ON CONFLICT DO NOTHING RETURNING *""",
                (data["task_id"],data["tenant_id"],data["workspace_id"],data["objective"],
                 data["acceptance_criteria"],data["idempotency_key"],data["status"],data["revision"],
                 data["repository_ref"],data["base_revision"],data["metadata"],data["evidence"],data["fingerprint"]))
            inserted = self._task(self._dict(cur, cur.fetchone()))
            if inserted is not None: return inserted
            cur.execute(f"SELECT * FROM {self.TABLE} WHERE tenant_id=%s AND workspace_id=%s AND idempotency_key=%s",
                        (task.tenant_id,task.workspace_id,task.idempotency_key))
            existing = self._task(self._dict(cur, cur.fetchone()))
            if existing is None: raise EngineeringTaskConflict("task_id_collision_or_create_race")
            if existing.fingerprint() != task.fingerprint():
                raise EngineeringTaskConflict("idempotency_key_reused_with_different_task")
            return existing
        return self._run(op, write=True)

    def get(self, task_id: str, *, tenant_id: str, workspace_id: str) -> EngineeringTaskContract:
        if not tenant_id or not workspace_id: raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        def op(_conn: Any, cur: Any) -> EngineeringTaskContract:
            cur.execute(f"SELECT * FROM {self.TABLE} WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s",
                        (task_id,tenant_id,workspace_id))
            task = self._task(self._dict(cur,cur.fetchone()))
            if task is None: raise EngineeringTaskNotFound("engineering_task_not_found")
            return task
        return self._run(op)

    def _list(self, where: str, params: tuple[Any, ...], limit: int) -> list[EngineeringTaskContract]:
        if not 1 <= limit <= 500: raise ValueError("limit_must_be_between_1_and_500")
        def op(_conn: Any, cur: Any) -> list[EngineeringTaskContract]:
            cur.execute(f"SELECT * FROM {self.TABLE} WHERE {where} ORDER BY updated_at DESC,task_id LIMIT %s", (*params,limit))
            return [self._task(self._dict(cur,row)) for row in cur.fetchall()]
        return self._run(op)

    def list_scope(self, *, tenant_id: str, workspace_id: str, limit: int = 100) -> list[EngineeringTaskContract]:
        if not tenant_id or not workspace_id: raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        return self._list("tenant_id=%s AND workspace_id=%s",(tenant_id,workspace_id),limit)

    def list_status(self, status: EngineeringTaskStatus, *, tenant_id: str, workspace_id: str,
                    limit: int = 100) -> list[EngineeringTaskContract]:
        if not tenant_id or not workspace_id: raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        if not 1 <= limit <= 500: raise ValueError("limit_must_be_between_1_and_500")
        def op(_conn: Any, cur: Any) -> list[EngineeringTaskContract]:
            cur.execute(f"SELECT * FROM {self.TABLE} WHERE tenant_id=%s AND workspace_id=%s AND status=%s ORDER BY updated_at,task_id LIMIT %s",
                        (tenant_id,workspace_id,status.value,limit))
            return [self._task(self._dict(cur,row)) for row in cur.fetchall()]
        return self._run(op)

    def _mutate(self, task_id: str, *, tenant_id: str, workspace_id: str, expected_revision: int,
                next_status: EngineeringTaskStatus | None = None, evidence: dict | None = None) -> EngineeringTaskContract:
        if not tenant_id or not workspace_id: raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        def op(_conn: Any, cur: Any) -> EngineeringTaskContract:
            cur.execute(f"SELECT * FROM {self.TABLE} WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s FOR UPDATE",
                        (task_id,tenant_id,workspace_id))
            current = self._task(self._dict(cur,cur.fetchone()))
            if current is None: raise EngineeringTaskNotFound("engineering_task_not_found")
            if current.revision != expected_revision: raise EngineeringTaskConflict("stale_revision")
            if next_status is not None:
                updated = transition_engineering_task(current,next_status,evidence=evidence,
                    tenant_id=tenant_id,workspace_id=workspace_id)
            else:
                if not isinstance(evidence,dict): raise ValueError("evidence_must_be_dict")
                merged = dict(current.evidence); merged.update(evidence)
                updated = replace(current,evidence=merged,revision=current.revision+1)
            data = _encode(updated)
            cur.execute(f"""UPDATE {self.TABLE} SET status=%s,revision=%s,evidence=%s::jsonb,fingerprint=%s,updated_at=now()
                WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s AND revision=%s RETURNING task_id""",
                (data["status"],data["revision"],data["evidence"],data["fingerprint"],task_id,tenant_id,workspace_id,expected_revision))
            if cur.fetchone() is None: raise EngineeringTaskConflict("compare_and_swap_failed")
            return updated
        return self._run(op,write=True)

    def transition(self, task_id: str, next_status: EngineeringTaskStatus, *, tenant_id: str, workspace_id: str,
                   expected_revision: int, evidence: dict | None = None) -> EngineeringTaskContract:
        return self._mutate(task_id,tenant_id=tenant_id,workspace_id=workspace_id,
            expected_revision=expected_revision,next_status=next_status,evidence=evidence)

    def review_plan_atomically(self, task_id: str, *, tenant_id: str, workspace_id: str,
                               expected_revision: int, reviewer_id: str, approve: bool,
                               reason: str = "") -> EngineeringTaskContract:
        """Commit task approval state and immutable approval ledger in one DB transaction.

        Authorization is deliberately performed by the workflow before this call; this
        method revalidates all task/plan invariants while holding the task row lock.
        It never grants merge, deployment, or release authority.
        """
        if not tenant_id or not workspace_id or not reviewer_id.strip():
            raise EngineeringTaskScopeError("tenant_workspace_and_reviewer_required")
        if not isinstance(approve, bool):
            raise ValueError("approval_decision_must_be_boolean")
        decision = "approved" if approve else "rejected"
        next_status = EngineeringTaskStatus.PLANNED if approve else EngineeringTaskStatus.BLOCKED
        def op(_conn: Any, cur: Any) -> EngineeringTaskContract:
            cur.execute(f"SELECT * FROM {self.TABLE} WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s FOR UPDATE",
                        (task_id, tenant_id, workspace_id))
            current = self._task(self._dict(cur, cur.fetchone()))
            if current is None:
                raise EngineeringTaskNotFound("engineering_task_not_found")
            if current.revision != expected_revision:
                raise EngineeringTaskConflict("stale_revision")
            review = dict(current.evidence.get("plan_review", {}))
            if current.status != EngineeringTaskStatus.PROPOSED or review.get("state") != "submitted":
                raise Phase162Error("submitted_plan_required")
            # The authenticated workspace operator may approve their own submitted plan.
            assignments = review.get("assignments")
            if assignments is not None:
                assignment_digest = hashlib.sha256(
                    json.dumps(assignments, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                if assignment_digest != review.get("assignments_digest"):
                    raise Phase162Error("submitted_assignments_fingerprint_mismatch")
            steps = review.get("steps", [])
            digest = _plan_digest(steps) if isinstance(steps, list) and steps else ""
            if not digest or digest != review.get("digest"):
                raise Phase162Error("submitted_plan_fingerprint_mismatch")
            review.update({"state": decision, "reviewed_by": reviewer_id,
                           "reason": str(reason)[:1000],
                           "approved_digest": digest if approve else None})
            updated = transition_engineering_task(current, next_status,
                evidence={"plan_review": review}, tenant_id=tenant_id, workspace_id=workspace_id)
            data = _encode(updated)
            cur.execute(f"""UPDATE {self.TABLE} SET status=%s,revision=%s,evidence=%s::jsonb,
                fingerprint=%s,updated_at=now() WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s
                AND revision=%s RETURNING task_id""",
                (data["status"], data["revision"], data["evidence"], data["fingerprint"],
                 task_id, tenant_id, workspace_id, expected_revision))
            if cur.fetchone() is None:
                raise EngineeringTaskConflict("compare_and_swap_failed")
            cur.execute("""INSERT INTO public.kdo_engineering_plan_reviews
                (tenant_id,workspace_id,task_id,plan_digest,reviewer_id,decision,reason)
                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant_id,workspace_id,task_id)
                DO NOTHING RETURNING task_id""",
                (tenant_id, workspace_id, task_id, digest, reviewer_id, decision, str(reason)[:1000]))
            if cur.fetchone() is None:
                raise Phase162Error("plan_review_already_recorded")
            return updated
        return self._run(op, write=True)

    def update_evidence(self, task_id: str, *, tenant_id: str, workspace_id: str,
                        expected_revision: int, evidence: dict) -> EngineeringTaskContract:
        return self._mutate(task_id,tenant_id=tenant_id,workspace_id=workspace_id,
            expected_revision=expected_revision,evidence=evidence)


class PostgresEngineeringWorkspaceRepository(_PostgresAdapter):
    """Shared workspace metadata and immutable one-time plan-review ledger."""
    WORKSPACES = "public.kdo_engineering_workspaces"
    REVIEWS = "public.kdo_engineering_plan_reviews"

    def put(self, *, tenant_id: str, workspace_id: str, task_id: str, path: str,
            branch: str, base_revision: str, head_revision: str, status: str = "active") -> WorkspaceRecord:
        if not all(isinstance(x,str) and x.strip() for x in (tenant_id,workspace_id,task_id,path,branch,base_revision,head_revision,status)):
            raise ValueError("complete_workspace_metadata_required")
        def op(_conn: Any, cur: Any) -> None:
            cur.execute(f"SELECT * FROM {self.WORKSPACES} WHERE tenant_id=%s AND workspace_id=%s AND task_id=%s FOR UPDATE",
                        (tenant_id,workspace_id,task_id))
            old = self._dict(cur,cur.fetchone())
            if old:
                if old["path"] != path or old["branch"] != branch or old["base_revision"] != base_revision:
                    raise Phase162Error("workspace_metadata_conflict")
                cur.execute(f"UPDATE {self.WORKSPACES} SET head_revision=%s,status=%s,updated_at=now() WHERE tenant_id=%s AND workspace_id=%s AND task_id=%s",
                            (head_revision,status,tenant_id,workspace_id,task_id))
            else:
                cur.execute(f"INSERT INTO {self.WORKSPACES}(tenant_id,workspace_id,task_id,path,branch,base_revision,head_revision,status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                            (tenant_id,workspace_id,task_id,path,branch,base_revision,head_revision,status))
        self._run(op,write=True)
        return self.get(task_id,tenant_id=tenant_id,workspace_id=workspace_id)

    @staticmethod
    def _workspace(row: dict[str, Any] | None) -> WorkspaceRecord | None:
        if row is None: return None
        if hasattr(row.get("updated_at"),"isoformat"): row["updated_at"] = row["updated_at"].isoformat()
        return WorkspaceRecord(**row)

    def get(self, task_id: str, *, tenant_id: str, workspace_id: str) -> WorkspaceRecord:
        if not tenant_id or not workspace_id: raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        def op(_conn: Any,cur: Any) -> WorkspaceRecord:
            cur.execute(f"SELECT * FROM {self.WORKSPACES} WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s",(task_id,tenant_id,workspace_id))
            record = self._workspace(self._dict(cur,cur.fetchone()))
            if record is None: raise EngineeringTaskNotFound("engineering_workspace_not_found")
            return record
        return self._run(op)

    def list_scope(self, *, tenant_id: str, workspace_id: str, limit: int = 100) -> list[WorkspaceRecord]:
        if not tenant_id or not workspace_id: raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        if not 1 <= limit <= 500: raise ValueError("limit_must_be_between_1_and_500")
        def op(_conn: Any,cur: Any) -> list[WorkspaceRecord]:
            cur.execute(f"SELECT * FROM {self.WORKSPACES} WHERE tenant_id=%s AND workspace_id=%s ORDER BY updated_at,task_id LIMIT %s",(tenant_id,workspace_id,limit))
            return [self._workspace(self._dict(cur,row)) for row in cur.fetchall()]
        return self._run(op)

    def mark_status(self, task_id: str, *, tenant_id: str, workspace_id: str, status: str,
                    head_revision: str | None = None) -> WorkspaceRecord:
        if not status: raise ValueError("workspace_status_required")
        def op(_conn: Any,cur: Any) -> None:
            if head_revision is None:
                cur.execute(f"UPDATE {self.WORKSPACES} SET status=%s,updated_at=now() WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s RETURNING task_id",(status,task_id,tenant_id,workspace_id))
            else:
                cur.execute(f"UPDATE {self.WORKSPACES} SET status=%s,head_revision=%s,updated_at=now() WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s RETURNING task_id",(status,head_revision,task_id,tenant_id,workspace_id))
            if cur.fetchone() is None: raise EngineeringTaskNotFound("engineering_workspace_not_found")
        self._run(op,write=True)
        return self.get(task_id,tenant_id=tenant_id,workspace_id=workspace_id)

    def record_plan_review(self, *, tenant_id: str, workspace_id: str, task_id: str,
                           plan_digest: str, reviewer_id: str, decision: str, reason: str = "") -> None:
        if decision not in {"approved","rejected"}: raise ValueError("invalid_plan_decision")
        if len(plan_digest) != 64 or any(c not in "0123456789abcdef" for c in plan_digest.lower()): raise ValueError("invalid_plan_digest")
        if not all(isinstance(x,str) and x.strip() for x in (tenant_id,workspace_id,task_id,reviewer_id)): raise ValueError("review_scope_and_reviewer_required")
        def op(_conn: Any,cur: Any) -> None:
            cur.execute(f"INSERT INTO {self.REVIEWS}(tenant_id,workspace_id,task_id,plan_digest,reviewer_id,decision,reason) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant_id,workspace_id,task_id) DO NOTHING RETURNING task_id",
                (tenant_id,workspace_id,task_id,plan_digest,reviewer_id,decision,reason[:1000]))
            if cur.fetchone() is None: raise Phase162Error("plan_review_already_recorded")
        self._run(op,write=True)

    def get_plan_review(self, task_id: str, *, tenant_id: str, workspace_id: str) -> dict[str, str]:
        if not tenant_id or not workspace_id: raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        def op(_conn: Any,cur: Any) -> dict[str,str]:
            cur.execute(f"SELECT * FROM {self.REVIEWS} WHERE task_id=%s AND tenant_id=%s AND workspace_id=%s",(task_id,tenant_id,workspace_id))
            row = self._dict(cur,cur.fetchone())
            if row is None: raise EngineeringTaskNotFound("engineering_plan_review_not_found")
            return {k:(v.isoformat() if hasattr(v,"isoformat") else str(v)) for k,v in row.items()}
        return self._run(op)
