"""Phase 16.4: shared PostgreSQL queue adapter for governed engineering jobs.

This adapter deliberately accepts an injected DB-API connection factory. It does
not silently fall back to SQLite, discover credentials, or connect to production
by itself. Apply the companion migration first and pass a server-side connection
factory backed by the canonical database. The caller owns secret management.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from services.kdo_engineering_phase16_3 import (
    EngineeringBuildJob,
    Phase163Error,
    _parse_time,
    _utcnow,
)


class Phase164Error(RuntimeError):
    """Shared queue configuration or persistence failure."""


def _row_dict(cursor: Any, row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys"):
        return dict(row)
    description = cursor.description or ()
    names = [getattr(col, "name", None) or col[0] for col in description]
    return dict(zip(names, row))


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


class PostgresEngineeringBuildQueue:
    """PostgreSQL queue using row locks, SKIP LOCKED, and lease-token fencing.

    A new database connection is opened for every operation so the adapter is
    safe to share across worker threads. The injected factory must return a
    PostgreSQL DB-API connection (psycopg 3 or psycopg2). Schema creation is
    intentionally migration-managed, not performed at application startup.
    """

    TABLE = "public.kdo_engineering_build_jobs"

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        if not callable(connection_factory):
            raise Phase164Error("postgres_connection_factory_required")
        self._connection_factory = connection_factory

    def _run(self, operation: Callable[[Any, Any], Any], *, write: bool = False) -> Any:
        connection = None
        cursor = None
        try:
            connection = self._connection_factory()
            cursor = connection.cursor()
            result = operation(connection, cursor)
            if write:
                connection.commit()
            return result
        except Phase163Error:
            if connection is not None:
                connection.rollback()
            raise
        except Exception as exc:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            raise Phase164Error("postgres_build_queue_operation_failed") from exc
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

    @staticmethod
    def _record(row: dict[str, Any] | None) -> EngineeringBuildJob | None:
        if row is None:
            return None
        for key in ("created_at", "updated_at", "lease_expires_at"):
            row[key] = _iso(row.get(key))
        return EngineeringBuildJob(**row)

    def enqueue(self, *, tenant_id: str, workspace_id: str, task_id: str,
                plan_digest: str, idempotency_key: str) -> EngineeringBuildJob:
        if not all(isinstance(v, str) and v.strip() for v in
                   (tenant_id, workspace_id, task_id, plan_digest, idempotency_key)):
            raise Phase163Error("complete_job_scope_and_digest_required")
        if len(plan_digest) != 64 or any(c not in "0123456789abcdef" for c in plan_digest.lower()):
            raise Phase163Error("invalid_plan_digest")

        def op(_conn: Any, cur: Any) -> EngineeringBuildJob:
            cur.execute("""SELECT * FROM public.kdo_engineering_build_jobs
                WHERE tenant_id=%s AND workspace_id=%s AND idempotency_key=%s FOR UPDATE""",
                (tenant_id, workspace_id, idempotency_key))
            existing = self._record(_row_dict(cur, cur.fetchone()))
            if existing:
                if existing.task_id != task_id or existing.plan_digest != plan_digest:
                    raise Phase163Error("idempotency_key_payload_conflict")
                return existing
            cur.execute("""SELECT * FROM public.kdo_engineering_build_jobs
                WHERE tenant_id=%s AND workspace_id=%s AND task_id=%s FOR UPDATE""",
                (tenant_id, workspace_id, task_id))
            task_existing = self._record(_row_dict(cur, cur.fetchone()))
            if task_existing:
                if task_existing.plan_digest == plan_digest:
                    return task_existing
                raise Phase163Error("task_already_has_build_job")
            job_id = "kdojob_" + secrets.token_hex(16)
            cur.execute("""INSERT INTO public.kdo_engineering_build_jobs
                (job_id,tenant_id,workspace_id,task_id,plan_digest,idempotency_key,status)
                VALUES (%s,%s,%s,%s,%s,%s,'queued') RETURNING *""",
                (job_id, tenant_id, workspace_id, task_id, plan_digest, idempotency_key))
            row = self._record(_row_dict(cur, cur.fetchone()))
            assert row is not None
            return row

        try:
            return self._run(op, write=True)
        except Phase164Error as exc:
            # A concurrent enqueue may win the unique constraint after our
            # initial reads. Re-read only to resolve a same-payload idempotent
            # retry; never convert a different payload into success.
            if exc.__cause__ is None:
                raise
            try:
                found = self.get_by_idempotency(tenant_id=tenant_id, workspace_id=workspace_id,
                    idempotency_key=idempotency_key)
            except Exception:
                raise exc
            if found.task_id == task_id and found.plan_digest == plan_digest:
                return found
            raise Phase163Error("idempotency_key_payload_conflict") from exc

    def get_by_idempotency(self, *, tenant_id: str, workspace_id: str,
                           idempotency_key: str) -> EngineeringBuildJob:
        def op(_conn: Any, cur: Any) -> EngineeringBuildJob:
            cur.execute("""SELECT * FROM public.kdo_engineering_build_jobs
                WHERE tenant_id=%s AND workspace_id=%s AND idempotency_key=%s""",
                (tenant_id, workspace_id, idempotency_key))
            row = self._record(_row_dict(cur, cur.fetchone()))
            if row is None:
                raise Phase163Error("engineering_build_job_not_found")
            return row
        return self._run(op)

    def get(self, job_id: str, *, tenant_id: str, workspace_id: str) -> EngineeringBuildJob:
        if not tenant_id or not workspace_id:
            raise Phase163Error("tenant_and_workspace_scope_required")
        def op(_conn: Any, cur: Any) -> EngineeringBuildJob:
            cur.execute("""SELECT * FROM public.kdo_engineering_build_jobs
                WHERE job_id=%s AND tenant_id=%s AND workspace_id=%s""",
                (job_id, tenant_id, workspace_id))
            row = self._record(_row_dict(cur, cur.fetchone()))
            if row is None:
                raise Phase163Error("engineering_build_job_not_found")
            return row
        return self._run(op)

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
        def op(_conn: Any, cur: Any) -> EngineeringBuildJob | None:
            # SKIP LOCKED permits multiple workers to claim independent jobs.
            # Tenant-bound workers filter scope in the row-locking query itself.
            if scoped:
                cur.execute("""SELECT * FROM public.kdo_engineering_build_jobs
                    WHERE status='queued' AND tenant_id=%s AND workspace_id=%s
                    ORDER BY created_at,job_id LIMIT 1
                    FOR UPDATE SKIP LOCKED""", (tenant_id, workspace_id))
            else:
                cur.execute("""SELECT * FROM public.kdo_engineering_build_jobs
                    WHERE status='queued' ORDER BY created_at,job_id LIMIT 1
                    FOR UPDATE SKIP LOCKED""")
            selected = self._record(_row_dict(cur, cur.fetchone()))
            if selected is None:
                return None
            token = secrets.token_hex(24)
            cur.execute("""UPDATE public.kdo_engineering_build_jobs
                SET status='running',attempts=attempts+1,lease_owner=%s,lease_token=%s,
                    lease_expires_at=now()+(%s * interval '1 second'),updated_at=now()
                WHERE job_id=%s AND status='queued' RETURNING *""",
                (worker_id, token, lease_seconds, selected.job_id))
            claimed = self._record(_row_dict(cur, cur.fetchone()))
            if claimed is None:
                raise Phase163Error("job_claim_race_retry")
            return claimed
        return self._run(op, write=True)

    def renew(self, job: EngineeringBuildJob, *, worker_id: str, lease_token: str,
              lease_seconds: int = 300) -> EngineeringBuildJob:
        if not 30 <= lease_seconds <= 3600:
            raise Phase163Error("lease_seconds_must_be_between_30_and_3600")
        def op(_conn: Any, cur: Any) -> EngineeringBuildJob:
            cur.execute("""UPDATE public.kdo_engineering_build_jobs
                SET lease_expires_at=now()+(%s * interval '1 second'),updated_at=now()
                WHERE job_id=%s AND tenant_id=%s AND workspace_id=%s AND task_id=%s
                  AND plan_digest=%s AND idempotency_key=%s AND status='running'
                  AND lease_owner=%s AND lease_token=%s AND lease_expires_at>now()
                RETURNING *""", (lease_seconds, job.job_id, job.tenant_id, job.workspace_id,
                    job.task_id, job.plan_digest, job.idempotency_key, worker_id, lease_token))
            row = self._record(_row_dict(cur, cur.fetchone()))
            if row is None:
                raise Phase163Error("worker_lease_fence_rejected_or_expired")
            return row
        return self._run(op, write=True)

    def complete(self, job: EngineeringBuildJob, *, worker_id: str, lease_token: str,
                 status: str) -> EngineeringBuildJob:
        if status not in {"awaiting_review", "failed", "uncertain"}:
            raise Phase163Error("invalid_worker_terminal_status")
        def op(_conn: Any, cur: Any) -> EngineeringBuildJob:
            cur.execute("""UPDATE public.kdo_engineering_build_jobs
                SET status=%s,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=now()
                WHERE job_id=%s AND tenant_id=%s AND workspace_id=%s AND task_id=%s
                  AND plan_digest=%s AND idempotency_key=%s AND status='running'
                  AND lease_owner=%s AND lease_token=%s AND lease_expires_at>now()
                RETURNING *""", (status, job.job_id, job.tenant_id, job.workspace_id,
                    job.task_id, job.plan_digest, job.idempotency_key, worker_id, lease_token))
            row = self._record(_row_dict(cur, cur.fetchone()))
            if row is None:
                raise Phase163Error("worker_lease_fence_rejected_or_expired_completion_uncertain")
            return row
        return self._run(op, write=True)

    def reconcile_expired(self, *, worker_is_alive: Callable[[EngineeringBuildJob], bool],
                          limit: int = 100) -> list[EngineeringBuildJob]:
        if not 1 <= limit <= 500:
            raise Phase163Error("limit_must_be_between_1_and_500")
        def read_expired(_conn: Any, cur: Any) -> list[EngineeringBuildJob]:
            cur.execute("""SELECT * FROM public.kdo_engineering_build_jobs
                WHERE status='running' AND lease_expires_at<=now()
                ORDER BY lease_expires_at,job_id LIMIT %s""", (limit,))
            return [self._record(_row_dict(cur, row)) for row in cur.fetchall()]
        candidates = self._run(read_expired)
        reconciled: list[EngineeringBuildJob] = []
        for job in candidates:
            try:
                alive = worker_is_alive(job)
            except Exception:
                alive = None
            if alive is not False:
                continue
            def mark_uncertain(_conn: Any, cur: Any) -> EngineeringBuildJob | None:
                cur.execute("""UPDATE public.kdo_engineering_build_jobs
                    SET status='uncertain',lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=now()
                    WHERE job_id=%s AND tenant_id=%s AND workspace_id=%s AND status='running'
                      AND lease_token=%s AND lease_expires_at<=now() RETURNING *""",
                    (job.job_id, job.tenant_id, job.workspace_id, job.lease_token))
                return self._record(_row_dict(cur, cur.fetchone()))
            updated = self._run(mark_uncertain, write=True)
            if updated is not None:
                reconciled.append(updated)
        return reconciled

    def close(self) -> None:
        """Compatibility with the SQLite adapter; connections are per-operation."""
        return None


def verify_shared_queue_configuration(*, queue: Any, action_kernel: Any,
                                      receipt_verifier: Callable[..., Any] | None) -> dict[str, Any]:
    """Fail-closed bootstrap check; it does not probe or mutate production systems."""
    registered = getattr(action_kernel, "registered_action_types", None)
    checks = {
        "postgres_queue_adapter": isinstance(queue, PostgresEngineeringBuildQueue),
        "action_kernel_register_api": callable(getattr(action_kernel, "register", None)),
        "action_kernel_contract_registry": callable(registered),
        "build_enqueue_contract_registered": bool(callable(registered) and "kdo_engineering_build_enqueue" in registered()),
        "receipt_verifier_configured": callable(receipt_verifier),
    }
    return {"ready": all(checks.values()), "checks": checks,
            "release_authorized": False,
            "note": "Configuration shape only; live database, governance and staging acceptance remain unverified."}


def register_postgres_build_enqueue_contract(action_kernel: Any, queue: PostgresEngineeringBuildQueue,
                                              *, action_type: str = "kdo_engineering_build_enqueue") -> None:
    """Explicitly register the existing high-risk Action Kernel contract to PostgreSQL.

    Registration is opt-in at application bootstrap. Council deliberation and the
    canonical receipt verifier remain required by the Phase 16.3 admission path.
    """
    if not isinstance(queue, PostgresEngineeringBuildQueue):
        raise Phase164Error("postgres_queue_adapter_required")
    if action_kernel is None or not callable(getattr(action_kernel, "register", None)):
        raise Phase164Error("action_kernel_required")
    registered = getattr(action_kernel, "registered_action_types", None)
    if not callable(registered):
        raise Phase164Error("action_kernel_registry_inspection_required")
    if action_type in registered():
        raise Phase164Error("build_enqueue_contract_already_registered")
    # The contract implementation is shared with Phase 16.3; its queue argument
    # is duck-typed and uses enqueue(...) only. This is explicit wiring, not fallback.
    from services.kdo_engineering_phase16_3 import register_engineering_build_enqueue_contract
    register_engineering_build_enqueue_contract(action_kernel, queue, action_type=action_type)
