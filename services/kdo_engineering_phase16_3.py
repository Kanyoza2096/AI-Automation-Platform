"""Phase 16.3: governed build admission and lease-fenced durable worker queue.

The SQLite queue is a durable single-host adapter for development/staging only.
Production must use the canonical shared database/queue and Action Kernel ledger.
This module never merges, pushes, deploys, or authorizes a release.
"""
from __future__ import annotations

import hashlib
import math
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from services.action_kernel import ActionContract
from services.kdo_engineering_governed_actions import (
    GovernedEngineeringActionError,
    execute_council_governed_engineering_action,
)
from services.kdo_engineering_phase16_2 import KDOPhase162Workflow, Phase162Error
from services.kdo_engineering_task_repository import EngineeringTaskNotFound
from services.kdo_engineering_vertical_contracts import EngineeringTaskStatus


class Phase163Error(RuntimeError):
    """Governance, queue, lease or worker lifecycle failure."""


@dataclass(frozen=True)
class EngineeringBuildJob:
    job_id: str
    tenant_id: str
    workspace_id: str
    task_id: str
    plan_digest: str
    idempotency_key: str
    status: str
    attempts: int
    lease_owner: str | None
    lease_token: str | None
    lease_expires_at: str | None
    created_at: str
    updated_at: str
    admission_decision_id: str | None = None
    admission_intent_id: str | None = None
    admission_execution_id: str | None = None
    admission_receipt_verified: bool = False
    admitted_at: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class SQLiteEngineeringBuildQueue:
    """Durable idempotent queue with owner/token fencing and conservative recovery."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None, timeout=30)
        self._db.row_factory = sqlite3.Row
        if self.db_path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript("""
          CREATE TABLE IF NOT EXISTS kdo_engineering_build_jobs (
            job_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL, workspace_id TEXT NOT NULL, task_id TEXT NOT NULL,
            plan_digest TEXT NOT NULL, idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
            lease_owner TEXT, lease_token TEXT, lease_expires_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            admission_decision_id TEXT, admission_intent_id TEXT, admission_execution_id TEXT,
            admission_receipt_verified INTEGER NOT NULL DEFAULT 0, admitted_at TEXT,
            UNIQUE(tenant_id, workspace_id, idempotency_key),
            UNIQUE(tenant_id, workspace_id, task_id)
          );
          CREATE INDEX IF NOT EXISTS idx_kdo_build_jobs_ready
            ON kdo_engineering_build_jobs(status, created_at);
          CREATE INDEX IF NOT EXISTS idx_kdo_build_jobs_scope
            ON kdo_engineering_build_jobs(tenant_id, workspace_id, status);
        """)
        # Additive upgrade for durable local queues created before receipt gating.
        existing = {row[1] for row in self._db.execute(
            "PRAGMA table_info(kdo_engineering_build_jobs)"
        ).fetchall()}
        for name, declaration in (
            ("admission_decision_id", "TEXT"),
            ("admission_intent_id", "TEXT"),
            ("admission_execution_id", "TEXT"),
            ("admission_receipt_verified", "INTEGER NOT NULL DEFAULT 0"),
            ("admitted_at", "TEXT"),
        ):
            if name not in existing:
                self._db.execute(
                    f"ALTER TABLE kdo_engineering_build_jobs ADD COLUMN {name} {declaration}"
                )

    @staticmethod
    def _record(row: sqlite3.Row) -> EngineeringBuildJob:
        values = dict(row)
        values["admission_receipt_verified"] = values.get("admission_receipt_verified") == 1
        return EngineeringBuildJob(**values)

    def enqueue(self, *, tenant_id: str, workspace_id: str, task_id: str,
                plan_digest: str, idempotency_key: str) -> EngineeringBuildJob:
        if not all(isinstance(x, str) and x.strip() for x in
                   (tenant_id, workspace_id, task_id, plan_digest, idempotency_key)):
            raise Phase163Error("complete_job_scope_and_digest_required")
        if len(plan_digest) != 64 or any(c not in "0123456789abcdef" for c in plan_digest.lower()):
            raise Phase163Error("invalid_plan_digest")
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                existing = self._db.execute("SELECT * FROM kdo_engineering_build_jobs WHERE tenant_id=? AND workspace_id=? AND idempotency_key=?",
                    (tenant_id, workspace_id, idempotency_key)).fetchone()
                if existing:
                    if (existing["task_id"] != task_id or existing["plan_digest"] != plan_digest):
                        raise Phase163Error("idempotency_key_payload_conflict")
                    self._db.execute("COMMIT")
                    return self._record(existing)
                task_existing = self._db.execute("SELECT * FROM kdo_engineering_build_jobs WHERE tenant_id=? AND workspace_id=? AND task_id=?",
                    (tenant_id, workspace_id, task_id)).fetchone()
                if task_existing:
                    if task_existing["plan_digest"] == plan_digest:
                        self._db.execute("COMMIT")
                        return self._record(task_existing)
                    raise Phase163Error("task_already_has_build_job")
                job_id = "kdojob_" + uuid.uuid4().hex
                self._db.execute("INSERT INTO kdo_engineering_build_jobs(job_id,tenant_id,workspace_id,task_id,plan_digest,idempotency_key,status) VALUES(?,?,?,?,?,?, 'queued')",
                    (job_id, tenant_id, workspace_id, task_id, plan_digest, idempotency_key))
                row = self._db.execute("SELECT * FROM kdo_engineering_build_jobs WHERE job_id=?", (job_id,)).fetchone()
                self._db.execute("COMMIT")
                return self._record(row)
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def record_admission_receipt(self, *, tenant_id: str, workspace_id: str, task_id: str,
                                 decision_id: str, intent_id: str, execution_id: str) -> EngineeringBuildJob:
        values = (tenant_id, workspace_id, task_id, decision_id, intent_id, execution_id)
        if not all(isinstance(v, str) and v.strip() for v in values):
            raise Phase163Error("complete_admission_receipt_scope_required")
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                result = self._db.execute("""UPDATE kdo_engineering_build_jobs
                    SET admission_decision_id=?, admission_intent_id=?, admission_execution_id=?,
                        admission_receipt_verified=1, admitted_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                    WHERE tenant_id=? AND workspace_id=? AND task_id=? AND status='queued'
                      AND admission_receipt_verified=0""",
                    (decision_id, intent_id, execution_id, tenant_id, workspace_id, task_id))
                row = self._db.execute("SELECT * FROM kdo_engineering_build_jobs WHERE tenant_id=? AND workspace_id=? AND task_id=?",
                    (tenant_id, workspace_id, task_id)).fetchone()
                if row is None:
                    raise Phase163Error("queued_job_not_found_or_receipt_already_recorded")
                recorded = dict(row)
                expected = (decision_id, intent_id, execution_id)
                actual = (recorded.get("admission_decision_id"), recorded.get("admission_intent_id"),
                          recorded.get("admission_execution_id"))
                if result.rowcount != 1 and not (
                    recorded.get("admission_receipt_verified") == 1 and actual == expected
                ):
                    raise Phase163Error("admission_receipt_conflict_or_job_not_queued")
                self._db.execute("COMMIT")
                return self._record(row)
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def get(self, job_id: str, *, tenant_id: str, workspace_id: str) -> EngineeringBuildJob:
        if not tenant_id or not workspace_id:
            raise Phase163Error("tenant_and_workspace_scope_required")
        with self._lock:
            row = self._db.execute("SELECT * FROM kdo_engineering_build_jobs WHERE job_id=? AND tenant_id=? AND workspace_id=?",
                (job_id, tenant_id, workspace_id)).fetchone()
        if row is None:
            raise Phase163Error("engineering_build_job_not_found")
        return self._record(row)

    def claim_next(
        self, *, worker_id: str, lease_seconds: int = 300,
        tenant_id: str | None = None, workspace_id: str | None = None,
    ) -> EngineeringBuildJob | None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise Phase163Error("worker_identity_required")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 30 <= lease_seconds <= 3600:
            raise Phase163Error("lease_seconds_must_be_between_30_and_3600")
        if (tenant_id is None) != (workspace_id is None):
            raise Phase163Error("tenant_and_workspace_scope_must_be_provided_together")
        scoped = tenant_id is not None
        if scoped and (
            not isinstance(tenant_id, str) or not tenant_id.strip()
            or not isinstance(workspace_id, str) or not workspace_id.strip()
        ):
            raise Phase163Error("tenant_and_workspace_scope_required")
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                if scoped:
                    row = self._db.execute(
                        "SELECT * FROM kdo_engineering_build_jobs "
                        "WHERE status='queued' AND admission_receipt_verified=1 AND tenant_id=? AND workspace_id=? "
                        "ORDER BY created_at,job_id LIMIT 1",
                        (tenant_id, workspace_id),
                    ).fetchone()
                else:
                    row = self._db.execute(
                        "SELECT * FROM kdo_engineering_build_jobs WHERE status='queued' AND admission_receipt_verified=1 "
                        "ORDER BY created_at,job_id LIMIT 1"
                    ).fetchone()
                if row is None:
                    self._db.execute("COMMIT")
                    return None
                token = uuid.uuid4().hex
                expires = (_utcnow() + timedelta(seconds=lease_seconds)).isoformat()
                result = self._db.execute("UPDATE kdo_engineering_build_jobs SET status='running',attempts=attempts+1,lease_owner=?,lease_token=?,lease_expires_at=?,updated_at=CURRENT_TIMESTAMP WHERE job_id=? AND status='queued'",
                    (worker_id, token, expires, row["job_id"]))
                if result.rowcount != 1:
                    raise Phase163Error("job_claim_race_retry")
                claimed = self._db.execute("SELECT * FROM kdo_engineering_build_jobs WHERE job_id=?", (row["job_id"],)).fetchone()
                self._db.execute("COMMIT")
                return self._record(claimed)
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def renew(self, job: EngineeringBuildJob, *, worker_id: str, lease_token: str,
              lease_seconds: int = 300) -> EngineeringBuildJob:
        if not isinstance(worker_id, str) or not worker_id.strip() or not isinstance(lease_token, str) or not lease_token.strip():
            raise Phase163Error("worker_identity_and_lease_token_required")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 30 <= lease_seconds <= 3600:
            raise Phase163Error("lease_seconds_must_be_between_30_and_3600")
        now = _utcnow()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT * FROM kdo_engineering_build_jobs WHERE job_id=? AND tenant_id=? AND workspace_id=?",
                    (job.job_id, job.tenant_id, job.workspace_id)).fetchone()
                if (row is None or row["status"] != "running" or row["lease_owner"] != worker_id
                        or row["lease_token"] != lease_token or row["task_id"] != job.task_id
                        or row["plan_digest"] != job.plan_digest or row["idempotency_key"] != job.idempotency_key):
                    raise Phase163Error("worker_lease_fence_rejected")
                expiry = _parse_time(row["lease_expires_at"])
                if expiry is None or expiry <= now:
                    raise Phase163Error("worker_lease_expired")
                next_expiry = (now + timedelta(seconds=lease_seconds)).isoformat()
                self._db.execute("UPDATE kdo_engineering_build_jobs SET lease_expires_at=?,updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
                    (next_expiry, job.job_id))
                updated = self._db.execute("SELECT * FROM kdo_engineering_build_jobs WHERE job_id=?", (job.job_id,)).fetchone()
                self._db.execute("COMMIT")
                return self._record(updated)
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def complete(self, job: EngineeringBuildJob, *, worker_id: str, lease_token: str,
                 status: str) -> EngineeringBuildJob:
        if not isinstance(worker_id, str) or not worker_id.strip() or not isinstance(lease_token, str) or not lease_token.strip():
            raise Phase163Error("worker_identity_and_lease_token_required")
        if status not in {"awaiting_review", "failed", "uncertain"}:
            raise Phase163Error("invalid_worker_terminal_status")
        now = _utcnow()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT * FROM kdo_engineering_build_jobs WHERE job_id=? AND tenant_id=? AND workspace_id=?",
                    (job.job_id, job.tenant_id, job.workspace_id)).fetchone()
                if (row is None or row["status"] != "running" or row["lease_owner"] != worker_id
                        or row["lease_token"] != lease_token or row["task_id"] != job.task_id
                        or row["plan_digest"] != job.plan_digest or row["idempotency_key"] != job.idempotency_key):
                    raise Phase163Error("worker_lease_fence_rejected")
                expiry = _parse_time(row["lease_expires_at"])
                if expiry is None or expiry <= now:
                    raise Phase163Error("worker_lease_expired_completion_uncertain")
                self._db.execute("UPDATE kdo_engineering_build_jobs SET status=?,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
                    (status, job.job_id))
                updated = self._db.execute("SELECT * FROM kdo_engineering_build_jobs WHERE job_id=?", (job.job_id,)).fetchone()
                self._db.execute("COMMIT")
                return self._record(updated)
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def reconcile_expired(self, *, worker_is_alive: Callable[[EngineeringBuildJob], bool], limit: int = 100) -> list[EngineeringBuildJob]:
        if not 1 <= limit <= 500:
            raise Phase163Error("limit_must_be_between_1_and_500")
        now = _utcnow()
        with self._lock:
            rows = self._db.execute("SELECT * FROM kdo_engineering_build_jobs WHERE status='running' ORDER BY updated_at LIMIT ?", (limit,)).fetchall()
        updated_jobs = []
        for row in rows:
            job = self._record(row)
            expiry = _parse_time(job.lease_expires_at)
            if expiry is None or expiry > now:
                continue
            try:
                alive = worker_is_alive(job)
            except Exception:
                alive = None
            if alive is not False:
                continue
            with self._lock:
                result = self._db.execute("UPDATE kdo_engineering_build_jobs SET status='uncertain',lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE job_id=? AND status='running' AND lease_token=? AND lease_expires_at=?",
                    (job.job_id, job.lease_token, job.lease_expires_at))
            if result.rowcount == 1:
                updated_jobs.append(self.get(job.job_id, tenant_id=job.tenant_id, workspace_id=job.workspace_id))
        return updated_jobs

    def close(self) -> None:
        with self._lock:
            self._db.close()


def register_engineering_build_enqueue_contract(action_kernel: Any, queue: SQLiteEngineeringBuildQueue,
                                                *, action_type: str = "kdo_engineering_build_enqueue") -> None:
    """Explicitly wire the build admission contract into the canonical Action Kernel.

    Call during application bootstrap only after choosing the authoritative queue.
    This does not bypass Council: the contract is high-risk and requires a Council decision.
    """
    if action_kernel is None or not callable(getattr(action_kernel, "register", None)):
        raise Phase163Error("action_kernel_required")
    if action_type in action_kernel.registered_action_types():
        raise Phase163Error("build_enqueue_contract_already_registered")

    setattr(action_kernel, "_kdo_engineering_build_queue", queue)

    def enqueue_handler(parameters: Mapping[str, Any], _intent: Any) -> dict[str, Any]:
        job = queue.enqueue(tenant_id=str(parameters.get("tenant_id", "")),
            workspace_id=str(parameters.get("workspace_id", "")), task_id=str(parameters.get("task_id", "")),
            plan_digest=str(parameters.get("plan_digest", "")),
            idempotency_key=str(parameters.get("job_idempotency_key", "")))
        return {"status": "succeeded", "job_id": job.job_id, "provider_object_id": job.job_id, "provider_request_id": job.job_id, "queued": job.status == "queued"}

    action_kernel.register(ActionContract(action_type=action_type, risk_level="high",
        required_parameters=("tenant_id", "workspace_id", "task_id", "plan_digest", "job_idempotency_key"),
        requires_approval=True, inline_with_decision=True, handler=enqueue_handler))


async def admit_approved_build(*, workflow: KDOPhase162Workflow, action_kernel: Any,
                               task_id: str, tenant_id: str, workspace_id: str, actor_id: str,
                               agent_id: str, tool_name: str = "engineering-build-worker",
                               action_type: str = "kdo_engineering_build_enqueue",
                               event_store: Any = None,
                               council_deliberate: Callable[..., Any] | None = None,
                               receipt_verifier: Callable[[Mapping[str, Any], Mapping[str, Any]], Any] | None = None) -> dict[str, Any]:
    """Authorize scope, validate the persisted approved plan, then enqueue via Council + Action Kernel."""
    workflow._require(actor_id, "enqueue_build", tenant_id, workspace_id)
    task = workflow.tasks.get(task_id, tenant_id=tenant_id, workspace_id=workspace_id)
    review = task.evidence.get("plan_review", {})
    if task.status != EngineeringTaskStatus.PLANNED or review.get("state") != "approved":
        raise Phase163Error("approved_plan_required_before_enqueue")
    steps = review.get("steps", [])
    payload = json.dumps([str(step).strip() for step in steps], ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if not steps or review.get("approved_digest") != digest or review.get("digest") != digest:
        raise Phase163Error("approved_plan_fingerprint_mismatch")
    try:
        approval = workflow.workspaces.get_plan_review(task_id, tenant_id=tenant_id, workspace_id=workspace_id)
    except EngineeringTaskNotFound as exc:
        raise Phase163Error("durable_plan_approval_record_required") from exc
    if approval.get("decision") != "approved" or approval.get("plan_digest") != digest or approval.get("reviewer_id") != review.get("reviewed_by"):
        raise Phase163Error("durable_plan_approval_mismatch")
    queue = getattr(action_kernel, "_kdo_engineering_build_queue", None)
    if queue is None or not callable(getattr(queue, "record_admission_receipt", None)):
        raise Phase163Error("governed_build_queue_receipt_recorder_required")
    if not callable(receipt_verifier):
        raise Phase163Error("authoritative_admission_receipt_verifier_required")

    job_idempotency_key = f"kdo-build:{tenant_id}:{workspace_id}:{task_id}:{digest}"

    async def verify_and_persist_receipt(scope: Mapping[str, Any], receipt: Mapping[str, Any]) -> bool:
        if receipt_verifier is None:
            return False
        receipt_scope = {**dict(scope), "plan_digest": digest,
                         "job_idempotency_key": job_idempotency_key}
        verified = receipt_verifier(receipt_scope, receipt)
        if hasattr(verified, "__await__"):
            verified = await verified
        if verified is not True:
            return False
        queue.record_admission_receipt(tenant_id=tenant_id, workspace_id=workspace_id, task_id=task_id,
            decision_id=str(scope.get("decision_id", "")), intent_id=str(receipt.get("intent_id", "")),
            execution_id=str(receipt.get("execution_id", "")))
        return True

    result = await execute_council_governed_engineering_action(
        task={"tenant_id": tenant_id, "workspace_id": workspace_id, "task_id": task_id},
        action_type=action_type,
        parameters={"plan_digest": digest, "job_idempotency_key": job_idempotency_key},
        agent_id=agent_id, tool_name=tool_name, objective=f"Queue approved engineering build for task {task_id}",
        action_kernel=action_kernel, event_store=event_store,
        council_deliberate=council_deliberate, receipt_verifier=verify_and_persist_receipt,
        idempotency_key=f"kdo-build-admission:{tenant_id}:{workspace_id}:{task_id}:{digest}")
    if result.get("status") != "succeeded" or result.get("ok") is not True:
        raise GovernedEngineeringActionError("build_admission_receipt_not_verified")
    return result


def run_claimed_build_job(
    *, workflow: KDOPhase162Workflow, queue: Any, job: EngineeringBuildJob,
    worker_id: str, lease_token: str, actor_id: str,
    executor: Callable[..., Mapping[str, Any]],
    admission_receipt_verifier: Callable[[Mapping[str, Any], Mapping[str, Any]], Any],
    lease_seconds: int = 300,
    max_runtime_seconds: float = 1800,
    heartbeat_interval_seconds: float | None = None,
    cancellation_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Run one approved build under a renewable, fenced lease.

    The deadline is a soft execution bound: on timeout the durable job is marked
    uncertain and no automatic replay occurs. Python cannot safely kill an arbitrary
    executor thread; the lease guard prevents it from publishing a final review state
    when it eventually returns. Executors should still impose their own process-level
    command/container timeouts.
    """
    if not callable(admission_receipt_verifier):
        raise Phase163Error("authoritative_admission_receipt_verifier_required")
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 30 <= lease_seconds <= 3600:
        raise Phase163Error("lease_seconds_must_be_between_30_and_3600")
    if isinstance(max_runtime_seconds, bool) or not isinstance(max_runtime_seconds, (int, float)) \
            or not math.isfinite(max_runtime_seconds) or not 0 < max_runtime_seconds <= 86400:
        raise Phase163Error("max_runtime_seconds_must_be_between_0_and_86400")
    if heartbeat_interval_seconds is None:
        heartbeat_interval_seconds = lease_seconds / 3
    if isinstance(heartbeat_interval_seconds, bool) or not isinstance(heartbeat_interval_seconds, (int, float)) \
            or not math.isfinite(heartbeat_interval_seconds) or not 0 < heartbeat_interval_seconds < lease_seconds / 2:
        raise Phase163Error("heartbeat_interval_must_be_positive_and_less_than_half_lease")

    workflow._require(actor_id, "execute_build_worker", job.tenant_id, job.workspace_id)
    current = queue.get(job.job_id, tenant_id=job.tenant_id, workspace_id=job.workspace_id)
    if (current.admission_receipt_verified is not True or not current.admission_decision_id
            or not current.admission_intent_id or not current.admission_execution_id):
        raise Phase163Error("verified_governed_admission_receipt_required")
    admission_scope = {"tenant_id": current.tenant_id, "workspace_id": current.workspace_id,
                       "task_id": current.task_id, "decision_id": current.admission_decision_id,
                       "action_type": "kdo_engineering_build_enqueue",
                       "plan_digest": current.plan_digest,
                       "job_idempotency_key": current.idempotency_key}
    admission_receipt = {"intent_id": current.admission_intent_id,
                         "execution_id": current.admission_execution_id,
                         "status": "succeeded", "action_type": "kdo_engineering_build_enqueue"}
    try:
        receipt_valid = admission_receipt_verifier(admission_scope, admission_receipt)
        if hasattr(receipt_valid, "__await__"):
            raise Phase163Error("async_admission_receipt_verifier_not_supported_in_sync_worker")
    except Phase163Error:
        raise
    except Exception as exc:
        raise Phase163Error("admission_receipt_verification_failed_closed") from exc
    if receipt_valid is not True:
        raise Phase163Error("authoritative_admission_receipt_not_confirmed")
    if (current.status != "running" or current.lease_owner != worker_id
            or current.lease_token != lease_token or current.task_id != job.task_id
            or current.plan_digest != job.plan_digest or current.idempotency_key != job.idempotency_key):
        raise Phase163Error("worker_lease_fence_rejected")
    expiry = _parse_time(current.lease_expires_at)
    if expiry is None or expiry <= _utcnow():
        raise Phase163Error("worker_lease_expired")
    task = workflow.tasks.get(job.task_id, tenant_id=job.tenant_id, workspace_id=job.workspace_id)
    review = task.evidence.get("plan_review", {})
    digest = hashlib.sha256(json.dumps([str(s).strip() for s in review.get("steps", [])], ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    if task.status != EngineeringTaskStatus.PLANNED or review.get("state") != "approved" or digest != current.plan_digest or review.get("approved_digest") != digest:
        raise Phase163Error("job_plan_or_task_state_mismatch")
    try:
        approval = workflow.workspaces.get_plan_review(job.task_id, tenant_id=job.tenant_id, workspace_id=job.workspace_id)
    except EngineeringTaskNotFound as exc:
        raise Phase163Error("durable_plan_approval_record_required") from exc
    if (approval.get("decision") != "approved" or approval.get("plan_digest") != digest
            or approval.get("reviewer_id") != review.get("reviewed_by")):
        raise Phase163Error("durable_plan_approval_mismatch")

    fence_lock = threading.RLock()
    execution_cancelled = cancellation_event if cancellation_event is not None else threading.Event()
    if not isinstance(execution_cancelled, threading.Event):
        raise Phase163Error("execution_cancellation_event_invalid")
    stop_heartbeat = threading.Event()
    lease_lost = threading.Event()
    timed_out = threading.Event()
    execution_done = threading.Event()
    latest_job = [current]
    outcome: dict[str, Any] = {}
    heartbeat_error: list[BaseException] = []
    deadline = time.monotonic() + float(max_runtime_seconds)

    def verify_current_lease() -> EngineeringBuildJob:
        try:
            fresh = queue.get(job.job_id, tenant_id=job.tenant_id, workspace_id=job.workspace_id)
        except Exception as exc:
            lease_lost.set()
            execution_cancelled.set()
            raise Phase162Error("worker_lease_fence_rejected") from exc
        if (fresh.status != "running" or fresh.lease_owner != worker_id
                or fresh.lease_token != lease_token or fresh.task_id != job.task_id
                or fresh.plan_digest != job.plan_digest or fresh.idempotency_key != job.idempotency_key):
            lease_lost.set()
            execution_cancelled.set()
            raise Phase162Error("worker_lease_fence_rejected")
        fresh_expiry = _parse_time(fresh.lease_expires_at)
        if fresh_expiry is None or fresh_expiry <= _utcnow():
            lease_lost.set()
            execution_cancelled.set()
            raise Phase162Error("worker_lease_expired")
        latest_job[0] = fresh
        return fresh

    @contextmanager
    def execution_guard():
        # This lock serializes timeout/lease-loss with short finalization writes.
        # It is never held while the executor runs.
        with fence_lock:
            if execution_cancelled.is_set():
                raise Phase162Error("worker_execution_cancelled")
            if timed_out.is_set():
                raise Phase162Error("worker_execution_timeout")
            if lease_lost.is_set():
                raise Phase162Error("worker_lease_lost")
            if not execution_done.is_set() and time.monotonic() >= deadline:
                execution_cancelled.set()
                raise Phase162Error("worker_execution_timeout")
            verify_current_lease()
            yield

    def heartbeat_loop() -> None:
        while not stop_heartbeat.wait(float(heartbeat_interval_seconds)):
            with fence_lock:
                if stop_heartbeat.is_set():
                    return
                try:
                    latest_job[0] = queue.renew(
                        latest_job[0], worker_id=worker_id, lease_token=lease_token,
                        lease_seconds=lease_seconds,
                    )
                except BaseException as exc:
                    heartbeat_error.append(exc)
                    lease_lost.set()
                    execution_cancelled.set()
                    stop_heartbeat.set()
                    return

    def execute() -> None:
        try:
            worker_authorizer = getattr(executor, "worker_authorizer", None)
            if worker_authorizer is not None:
                binder = getattr(worker_authorizer, "bind_execution_context", None)
                if not callable(binder):
                    raise Phase163Error("worker_authorizer_context_binding_required")
                execution_context = {
                    "job_id": current.job_id,
                    "tenant_id": current.tenant_id,
                    "workspace_id": current.workspace_id,
                    "task_id": current.task_id,
                    "plan_digest": current.plan_digest,
                    "lease_token": lease_token,
                }
                with binder(execution_context, lambda: (verify_current_lease() is not None)):
                    outcome["result"] = workflow.execute_approved_build(
                        job.task_id, tenant_id=job.tenant_id, workspace_id=job.workspace_id,
                        actor_id=actor_id, executor=executor, execution_guard=execution_guard,
                        execution_cancelled=execution_cancelled,
                    )
            else:
                outcome["result"] = workflow.execute_approved_build(
                    job.task_id, tenant_id=job.tenant_id, workspace_id=job.workspace_id,
                    actor_id=actor_id, executor=executor, execution_guard=execution_guard,
                    execution_cancelled=execution_cancelled,
                )
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            execution_done.set()
            stop_heartbeat.set()

    heartbeat = threading.Thread(
        target=heartbeat_loop, name=f"kdo-lease-heartbeat-{job.job_id}", daemon=True
    )
    worker = threading.Thread(
        target=execute, name=f"kdo-engineering-worker-{job.job_id}", daemon=True
    )
    heartbeat.start()
    worker.start()

    if not execution_done.wait(float(max_runtime_seconds)):
        timeout_error: BaseException | None = None
        with fence_lock:
            # If execution completed while the supervisor waited for the fence lock,
            # prefer its completed result over a spurious timeout.
            if not execution_done.is_set():
                timed_out.set()
                execution_cancelled.set()
                stop_heartbeat.set()
                try:
                    queue.complete(latest_job[0], worker_id=worker_id,
                                   lease_token=lease_token, status="uncertain")
                except BaseException as exc:
                    timeout_error = exc
        heartbeat.join(timeout=max(1.0, min(5.0, float(heartbeat_interval_seconds) * 2)))
        if not execution_done.is_set():
            if timeout_error is not None:
                raise Phase163Error(
                    "worker_execution_timeout_reconciliation_required"
                ) from timeout_error
            raise Phase163Error("worker_execution_timeout_marked_uncertain")

    stop_heartbeat.set()
    heartbeat.join(timeout=max(1.0, min(5.0, float(heartbeat_interval_seconds) * 2)))
    if heartbeat.is_alive():
        raise Phase163Error("worker_heartbeat_shutdown_timeout_reconciliation_required")

    if lease_lost.is_set():
        completion_error: BaseException | None = None
        with fence_lock:
            try:
                queue.complete(latest_job[0], worker_id=worker_id,
                               lease_token=lease_token, status="uncertain")
            except BaseException as exc:
                completion_error = exc
        cause = heartbeat_error[0] if heartbeat_error else completion_error
        raise Phase163Error("worker_lease_lost_during_execution") from cause

    if "error" in outcome:
        error = outcome["error"]
        with fence_lock:
            try:
                queue.complete(latest_job[0], worker_id=worker_id,
                               lease_token=lease_token, status="uncertain")
            except BaseException:
                pass
        # The worker can observe the deadline in its finalization guard and exit
        # just before the supervisor's wait expires. Normalize that race to the
        # same queue-level timeout outcome as the supervisor timeout path.
        if isinstance(error, Phase162Error) and str(error) == "worker_execution_timeout":
            raise Phase163Error("worker_execution_timeout_marked_uncertain") from error
        raise error

    result = outcome["result"]
    terminal = "awaiting_review" if result["task"].status == EngineeringTaskStatus.AWAITING_REVIEW else "failed"
    with fence_lock:
        verify_current_lease()
        queue.complete(latest_job[0], worker_id=worker_id, lease_token=lease_token, status=terminal)
    return result
