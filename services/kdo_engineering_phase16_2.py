"""Phase 16.2: persisted plan approval, isolated workspace execution and reconciliation.

SQLite repositories are local durability adapters, not a distributed production queue.
This module never merges, pushes, deploys, or authorizes a release.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from services.kdo_engineering_vertical_contracts import EngineeringTaskContract, EngineeringTaskStatus
from services.kdo_engineering_task_repository import (
    SQLiteEngineeringTaskRepository, EngineeringTaskConflict, EngineeringTaskNotFound,
    EngineeringTaskScopeError,
)
from services.kdo_engineering_workspaces import (
    EngineeringWorkspaceError, create_task_worktree, inspect_task_worktree,
)


class Phase162Error(RuntimeError):
    """Policy, lifecycle or reconciliation failure."""


@dataclass(frozen=True)
class WorkspaceRecord:
    tenant_id: str
    workspace_id: str
    task_id: str
    path: str
    branch: str
    base_revision: str
    head_revision: str
    status: str
    updated_at: str


class SQLiteEngineeringWorkspaceRepository:
    """Durable, scope-keyed workspace metadata with unique task/path constraints."""
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        if self.db_path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS kdo_engineering_workspaces (
              tenant_id TEXT NOT NULL, workspace_id TEXT NOT NULL, task_id TEXT NOT NULL,
              path TEXT NOT NULL UNIQUE, branch TEXT NOT NULL, base_revision TEXT NOT NULL,
              head_revision TEXT NOT NULL, status TEXT NOT NULL,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (tenant_id, workspace_id, task_id)
            );
            CREATE INDEX IF NOT EXISTS idx_kdo_workspaces_scope
              ON kdo_engineering_workspaces(tenant_id, workspace_id, status);
            CREATE TABLE IF NOT EXISTS kdo_engineering_plan_reviews (
              tenant_id TEXT NOT NULL, workspace_id TEXT NOT NULL, task_id TEXT NOT NULL,
              plan_digest TEXT NOT NULL, reviewer_id TEXT NOT NULL, decision TEXT NOT NULL,
              reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (tenant_id, workspace_id, task_id)
            );
        """)

    def put(self, *, tenant_id: str, workspace_id: str, task_id: str, path: str,
            branch: str, base_revision: str, head_revision: str, status: str = "active") -> WorkspaceRecord:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                old = self._db.execute("SELECT * FROM kdo_engineering_workspaces WHERE tenant_id=? AND workspace_id=? AND task_id=?",
                    (tenant_id, workspace_id, task_id)).fetchone()
                if old:
                    if old["path"] != path or old["branch"] != branch:
                        raise Phase162Error("workspace_metadata_conflict")
                    self._db.execute("UPDATE kdo_engineering_workspaces SET head_revision=?,status=?,updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND workspace_id=? AND task_id=?",
                        (head_revision, status, tenant_id, workspace_id, task_id))
                else:
                    self._db.execute("INSERT INTO kdo_engineering_workspaces(tenant_id,workspace_id,task_id,path,branch,base_revision,head_revision,status) VALUES(?,?,?,?,?,?,?,?)",
                        (tenant_id, workspace_id, task_id, path, branch, base_revision, head_revision, status))
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        return self.get(task_id, tenant_id=tenant_id, workspace_id=workspace_id)

    def get(self, task_id: str, *, tenant_id: str, workspace_id: str) -> WorkspaceRecord:
        if not tenant_id or not workspace_id:
            raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        with self._lock:
            row = self._db.execute("SELECT * FROM kdo_engineering_workspaces WHERE task_id=? AND tenant_id=? AND workspace_id=?",
                (task_id, tenant_id, workspace_id)).fetchone()
        if row is None:
            raise EngineeringTaskNotFound("engineering_workspace_not_found")
        return WorkspaceRecord(**dict(row))

    def list_scope(self, *, tenant_id: str, workspace_id: str, limit: int = 100) -> list[WorkspaceRecord]:
        if not tenant_id or not workspace_id:
            raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        if not 1 <= limit <= 500:
            raise ValueError("limit_must_be_between_1_and_500")
        with self._lock:
            rows = self._db.execute("SELECT * FROM kdo_engineering_workspaces WHERE tenant_id=? AND workspace_id=? ORDER BY updated_at,task_id LIMIT ?",
                (tenant_id, workspace_id, limit)).fetchall()
        return [WorkspaceRecord(**dict(row)) for row in rows]

    def mark_status(self, task_id: str, *, tenant_id: str, workspace_id: str, status: str,
                    head_revision: str | None = None) -> WorkspaceRecord:
        with self._lock:
            if head_revision is None:
                result = self._db.execute("UPDATE kdo_engineering_workspaces SET status=?,updated_at=CURRENT_TIMESTAMP WHERE task_id=? AND tenant_id=? AND workspace_id=?",
                    (status, task_id, tenant_id, workspace_id))
            else:
                result = self._db.execute("UPDATE kdo_engineering_workspaces SET status=?,head_revision=?,updated_at=CURRENT_TIMESTAMP WHERE task_id=? AND tenant_id=? AND workspace_id=?",
                    (status, head_revision, task_id, tenant_id, workspace_id))
            if result.rowcount != 1:
                raise EngineeringTaskNotFound("engineering_workspace_not_found")
        return self.get(task_id, tenant_id=tenant_id, workspace_id=workspace_id)

    def record_plan_review(self, *, tenant_id: str, workspace_id: str, task_id: str,
                           plan_digest: str, reviewer_id: str, decision: str, reason: str = "") -> None:
        if decision not in {"approved", "rejected"}:
            raise ValueError("invalid_plan_decision")
        with self._lock:
            try:
                self._db.execute("INSERT INTO kdo_engineering_plan_reviews(tenant_id,workspace_id,task_id,plan_digest,reviewer_id,decision,reason) VALUES(?,?,?,?,?,?,?)",
                    (tenant_id, workspace_id, task_id, plan_digest, reviewer_id, decision, reason[:1000]))
            except sqlite3.IntegrityError as exc:
                raise Phase162Error("plan_review_already_recorded") from exc

    def get_plan_review(self, task_id: str, *, tenant_id: str, workspace_id: str) -> dict[str, str]:
        if not tenant_id or not workspace_id:
            raise EngineeringTaskScopeError("tenant_and_workspace_scope_required")
        with self._lock:
            row = self._db.execute("SELECT * FROM kdo_engineering_plan_reviews WHERE task_id=? AND tenant_id=? AND workspace_id=?",
                (task_id, tenant_id, workspace_id)).fetchone()
        if row is None:
            raise EngineeringTaskNotFound("engineering_plan_review_not_found")
        return dict(row)

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _plan_digest(plan: Sequence[str]) -> str:
    payload = json.dumps([str(step).strip() for step in plan], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class KDOPhase162Workflow:
    """Orchestrates plan review -> approval -> isolated build; all actions are scope-authorized."""
    def __init__(self, *, task_repository: SQLiteEngineeringTaskRepository,
                 workspace_repository: SQLiteEngineeringWorkspaceRepository,
                 source_repository_path: str | Path, managed_root: str | Path,
                 authorize: Callable[[str, str, str, str], bool]) -> None:
        self.tasks = task_repository
        self.workspaces = workspace_repository
        self.source_repository_path = Path(source_repository_path).resolve()
        self.managed_root = Path(managed_root).resolve()
        self.authorize = authorize

    def _require(self, actor_id: str, action: str, tenant_id: str, workspace_id: str) -> None:
        if not all(isinstance(v, str) and v.strip() for v in (actor_id, tenant_id, workspace_id)):
            raise Phase162Error("actor_and_scope_required")
        try:
            allowed = self.authorize(actor_id, action, tenant_id, workspace_id)
        except Exception as exc:
            raise Phase162Error("authorization_unavailable") from exc
        if allowed is not True:
            raise Phase162Error("unauthorized_" + action)

    def create_task(self, task: EngineeringTaskContract, *, actor_id: str) -> EngineeringTaskContract:
        self._require(actor_id, "create_task", task.tenant_id, task.workspace_id)
        if task.status != EngineeringTaskStatus.PROPOSED or any(
            key in task.evidence for key in ("plan_review", "build_execution", "recovery")
        ):
            raise Phase162Error("new_tasks_must_be_unreviewed_proposals")
        # Repository uniqueness on (tenant, workspace, idempotency_key) makes retries idempotent.
        return self.tasks.create(task)

    def submit_plan(self, task_id: str, *, tenant_id: str, workspace_id: str, actor_id: str,
                    plan: Sequence[str]) -> EngineeringTaskContract:
        self._require(actor_id, "submit_plan", tenant_id, workspace_id)
        if not isinstance(plan, (list, tuple)) or not plan or any(not isinstance(s, str) or not s.strip() for s in plan):
            raise Phase162Error("plan_must_contain_nonempty_steps")
        task = self.tasks.get(task_id, tenant_id=tenant_id, workspace_id=workspace_id)
        if task.status != EngineeringTaskStatus.PROPOSED:
            raise Phase162Error("plan_submission_requires_proposed_task")
        if task.evidence.get("plan_review", {}).get("state") in {"submitted", "approved", "rejected"}:
            raise Phase162Error("plan_already_submitted")
        plan_record = {"state": "submitted", "steps": [s.strip() for s in plan],
                       "digest": _plan_digest(plan), "submitted_by": actor_id}
        return self.tasks.update_evidence(task_id, tenant_id=tenant_id, workspace_id=workspace_id,
            expected_revision=task.revision, evidence={"plan_review": plan_record})

    def review_plan(self, task_id: str, *, tenant_id: str, workspace_id: str,
                    reviewer_id: str, approve: bool, reason: str = "") -> EngineeringTaskContract:
        self._require(reviewer_id, "review_plan", tenant_id, workspace_id)
        task = self.tasks.get(task_id, tenant_id=tenant_id, workspace_id=workspace_id)
        review = dict(task.evidence.get("plan_review", {}))
        if task.status != EngineeringTaskStatus.PROPOSED or review.get("state") != "submitted":
            raise Phase162Error("submitted_plan_required")
        if review.get("submitted_by") == reviewer_id:
            raise Phase162Error("independent_plan_reviewer_required")
        review.update({"state": "approved" if approve else "rejected", "reviewed_by": reviewer_id,
                       "reason": str(reason)[:1000], "approved_digest": review["digest"] if approve else None})
        # PostgreSQL adapters atomically persist task state and the immutable approval ledger.
        # SQLite remains supported for local tests/development, but it uses its local transaction path.
        atomic_review = getattr(self.tasks, "review_plan_atomically", None)
        if callable(atomic_review):
            return atomic_review(task_id, tenant_id=tenant_id, workspace_id=workspace_id,
                expected_revision=task.revision, reviewer_id=reviewer_id, approve=approve, reason=reason)
        next_status = EngineeringTaskStatus.PLANNED if approve else EngineeringTaskStatus.BLOCKED
        updated = self.tasks.transition(task_id, next_status, tenant_id=tenant_id, workspace_id=workspace_id,
                                        expected_revision=task.revision, evidence={"plan_review": review})
        self.workspaces.record_plan_review(tenant_id=tenant_id, workspace_id=workspace_id, task_id=task_id,
            plan_digest=review["digest"], reviewer_id=reviewer_id, decision=review["state"], reason=reason)
        return updated

    def execute_approved_build(self, task_id: str, *, tenant_id: str, workspace_id: str,
                               actor_id: str, executor: Callable[[str, EngineeringTaskContract, Sequence[str]], Mapping[str, Any]],
                               execution_guard: Callable[[], Any] | None = None) -> dict[str, Any]:
        """Execute only in an approved task worktree; guard lease-sensitive checkpoints.

        execution_guard is a context-manager factory supplied by a durable worker.
        It is entered before execution and around final lifecycle/workspace writes so a
        worker whose lease has been lost cannot publish a review-ready task state.
        It is not an authorization grant and does not sandbox the executor.
        """
        guard = execution_guard or nullcontext
        with guard():
            pass
        self._require(actor_id, "execute_build", tenant_id, workspace_id)
        task = self.tasks.get(task_id, tenant_id=tenant_id, workspace_id=workspace_id)
        review = task.evidence.get("plan_review", {})
        if task.status != EngineeringTaskStatus.PLANNED or review.get("state") != "approved":
            raise Phase162Error("approved_plan_required_before_build")
        steps = review.get("steps", [])
        if not steps or review.get("approved_digest") != _plan_digest(steps):
            raise Phase162Error("approved_plan_fingerprint_mismatch")
        try:
            approval_record = self.workspaces.get_plan_review(task_id, tenant_id=tenant_id, workspace_id=workspace_id)
        except EngineeringTaskNotFound as exc:
            raise Phase162Error("durable_plan_approval_record_required") from exc
        if (approval_record["decision"] != "approved" or approval_record["plan_digest"] != review["digest"]
            or approval_record["reviewer_id"] != review.get("reviewed_by")):
            raise Phase162Error("durable_plan_approval_mismatch")
        # Repeated invocation cannot create or execute a second workspace/task run.
        try:
            self.workspaces.get(task_id, tenant_id=tenant_id, workspace_id=workspace_id)
        except EngineeringTaskNotFound:
            pass
        else:
            raise Phase162Error("task_workspace_already_registered")
        queued = self.tasks.transition(task_id, EngineeringTaskStatus.QUEUED,
            tenant_id=tenant_id, workspace_id=workspace_id, expected_revision=task.revision,
            evidence={"build_execution": {"state": "queued", "requested_by": actor_id}})
        running = self.tasks.transition(task_id, EngineeringTaskStatus.RUNNING,
            tenant_id=tenant_id, workspace_id=workspace_id, expected_revision=queued.revision,
            evidence={"build_execution": {"state": "running", "requested_by": actor_id}})
        try:
            with guard():
                pass
            wt = create_task_worktree(self.source_repository_path, self.managed_root,
                tenant_id=tenant_id, workspace_id=workspace_id, task_id=task_id,
                base_ref=task.base_revision or "HEAD")
            record = self.workspaces.put(tenant_id=tenant_id, workspace_id=workspace_id, task_id=task_id,
                path=wt.path, branch=wt.branch, base_revision=wt.base_revision,
                head_revision=wt.head_revision, status="active")
            with guard():
                pass
            output = executor(record.path, running, tuple(steps))
            if not isinstance(output, Mapping):
                raise Phase162Error("build_executor_must_return_mapping")
            with guard():
                pass
            snapshot = inspect_task_worktree(record.path, managed_root=self.managed_root)
            quality = output.get("quality_loop")
            if isinstance(quality, Mapping) and quality.get("status") != "awaiting_authorization":
                # A dynamic QA/repair loop that is blocked or inconclusive must not
                # be presented as an ordinary review-ready build. Preserve workspace
                # and evidence for diagnosis; never auto-resume or release.
                with guard():
                    self.workspaces.mark_status(task_id, tenant_id=tenant_id, workspace_id=workspace_id,
                        status="quality_blocked", head_revision=snapshot["revision"])
                    blocked = self.tasks.transition(task_id, EngineeringTaskStatus.BLOCKED,
                        tenant_id=tenant_id, workspace_id=workspace_id, expected_revision=running.revision,
                        evidence={"build_execution": {"state": "quality_blocked",
                            "workspace_path": record.path, "branch": record.branch,
                            "base_revision": record.base_revision, "head_revision": snapshot["revision"],
                            "output": dict(output), "release_authorized": False,
                            "automatic_resume": False, "workspace_cleanup_required": True}})
                return {"task": blocked, "workspace": self.workspaces.get(task_id, tenant_id=tenant_id, workspace_id=workspace_id),
                        "inspection": snapshot, "quality_loop": dict(quality)}
            with guard():
                self.workspaces.mark_status(task_id, tenant_id=tenant_id, workspace_id=workspace_id,
                    status="awaiting_review", head_revision=snapshot["revision"])
                final = self.tasks.transition(task_id, EngineeringTaskStatus.AWAITING_REVIEW,
                    tenant_id=tenant_id, workspace_id=workspace_id, expected_revision=running.revision,
                    evidence={"build_execution": {"state": "awaiting_review", "workspace_path": record.path,
                        "branch": record.branch, "base_revision": record.base_revision,
                        "head_revision": snapshot["revision"], "output": dict(output),
                        "release_authorized": False}})
            return {"task": final, "workspace": self.workspaces.get(task_id, tenant_id=tenant_id, workspace_id=workspace_id),
                    "inspection": snapshot}
        except Exception as exc:
            # Never auto-resume or delete the worktree on interruption/failure; preserve evidence for review.
            try:
                current = self.tasks.get(task_id, tenant_id=tenant_id, workspace_id=workspace_id)
                if current.status == EngineeringTaskStatus.RUNNING:
                    self.tasks.transition(task_id, EngineeringTaskStatus.BLOCKED,
                        tenant_id=tenant_id, workspace_id=workspace_id, expected_revision=current.revision,
                        evidence={"build_execution": {"state": "interrupted_or_failed", "error_type": type(exc).__name__,
                            "automatic_resume": False, "workspace_cleanup_required": True, "release_authorized": False}})
                self.workspaces.mark_status(task_id, tenant_id=tenant_id, workspace_id=workspace_id,
                    status="interrupted_or_failed")
            except (EngineeringTaskNotFound, EngineeringTaskConflict, EngineeringTaskScopeError):
                pass
            if isinstance(exc, (Phase162Error, EngineeringWorkspaceError)):
                raise
            raise Phase162Error("build_execution_failed_workspace_preserved") from exc

    def reconcile_workspaces(self, *, tenant_id: str, workspace_id: str, actor_id: str,
                             limit: int = 100,
                             worker_is_alive: Callable[[EngineeringTaskContract], bool] | None = None) -> list[dict[str, Any]]:
        """Inspect scoped records and mark missing/mismatched worktrees; never auto-delete or resume."""
        self._require(actor_id, "reconcile_workspaces", tenant_id, workspace_id)
        results = []
        for record in self.workspaces.list_scope(tenant_id=tenant_id, workspace_id=workspace_id, limit=limit):
            task = self.tasks.get(record.task_id, tenant_id=tenant_id, workspace_id=workspace_id)
            if task.status == EngineeringTaskStatus.RUNNING and worker_is_alive is not None:
                try:
                    alive = worker_is_alive(task)
                except Exception:
                    alive = None  # An unknown liveness result is never treated as a dead worker.
                if alive is False:
                    try:
                        task = self.tasks.transition(task.task_id, EngineeringTaskStatus.BLOCKED,
                            tenant_id=tenant_id, workspace_id=workspace_id, expected_revision=task.revision,
                            evidence={"recovery": {"status": "interrupted_worker_confirmed_absent",
                                "automatic_resume": False, "cleanup_required": True,
                                "reason": "worker_absence_confirmed; inspect workspace before retry"}})
                    except EngineeringTaskConflict:
                        task = self.tasks.get(record.task_id, tenant_id=tenant_id, workspace_id=workspace_id)
                    self.workspaces.mark_status(record.task_id, tenant_id=tenant_id, workspace_id=workspace_id,
                        status="interrupted_worker_confirmed_absent")
                    results.append({"task_id": record.task_id, "status": "interrupted_worker_confirmed_absent",
                                    "task_status": task.status.value, "automatic_resume": False,
                                    "automatic_cleanup": False})
                    continue
            try:
                snapshot = inspect_task_worktree(record.path, managed_root=self.managed_root)
                if (snapshot["branch"] != record.branch or snapshot["revision"] != record.head_revision
                        or len(snapshot["status"].splitlines()) > 1):
                    status = "drift_detected"
                else:
                    status = "reconciled"
                self.workspaces.mark_status(record.task_id, tenant_id=tenant_id, workspace_id=workspace_id,
                    status=status, head_revision=snapshot["revision"])
                results.append({"task_id": record.task_id, "status": status, "inspection": snapshot})
            except (EngineeringWorkspaceError, OSError):
                self.workspaces.mark_status(record.task_id, tenant_id=tenant_id, workspace_id=workspace_id,
                    status="workspace_missing_or_uninspectable")
                results.append({"task_id": record.task_id, "status": "workspace_missing_or_uninspectable",
                                "automatic_cleanup": False})
        return results
