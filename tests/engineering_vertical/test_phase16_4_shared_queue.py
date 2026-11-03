from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.kdo_engineering_phase16_3 import EngineeringBuildJob, Phase163Error
from services.kdo_engineering_phase16_4 import (
    Phase164Error,
    PostgresEngineeringBuildQueue,
    verify_shared_queue_configuration,
)

DIGEST = "a" * 64


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = None
        self._one = None
        self._all = []
        self.rowcount = 0
        self.last_sql = ""
        self.last_params = None

    def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.last_params = params
        self.connection.statements.append((self.last_sql, params))
        if "FOR UPDATE SKIP LOCKED" in self.last_sql:
            self._one = self.connection.next_job
            self.description = self.connection.description_for_job()
        elif "UPDATE public.kdo_engineering_build_jobs" in self.last_sql and "RETURNING *" in self.last_sql:
            if self.connection.return_updated:
                self._one = dict(self.connection.next_job or {})
                if self._one:
                    self._one.update({"status": "running", "lease_owner": params[0] if len(params) > 1 else "worker",
                                      "lease_token": params[1] if len(params) > 1 else "token",
                                      "lease_expires_at": datetime.now(timezone.utc) + timedelta(seconds=120)})
                self.description = self.connection.description_for_job()
            else:
                self._one = None
                self.description = self.connection.description_for_job()
        else:
            self._one = None
            self.description = self.connection.description_for_job()
        return self

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all

    def close(self):
        pass


class FakeConnection:
    def __init__(self, next_job=None, return_updated=True):
        self.next_job = next_job
        self.return_updated = return_updated
        self.statements = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    @staticmethod
    def description_for_job():
        return [(name,) for name in (
            "job_id", "tenant_id", "workspace_id", "task_id", "plan_digest", "idempotency_key",
            "status", "attempts", "lease_owner", "lease_token", "lease_expires_at", "created_at", "updated_at")]

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def job_row(status="queued"):
    now = datetime.now(timezone.utc)
    return {
        "job_id": "kdojob_test", "tenant_id": "tenant-a", "workspace_id": "workspace-a",
        "task_id": "task-a", "plan_digest": DIGEST, "idempotency_key": "idem-a",
        "status": status, "attempts": 0, "lease_owner": None, "lease_token": None,
        "lease_expires_at": None, "created_at": now, "updated_at": now,
    }


def test_postgres_adapter_requires_explicit_connection_factory():
    with pytest.raises(Phase164Error, match="connection_factory_required"):
        PostgresEngineeringBuildQueue(None)


def test_claim_uses_skip_locked_and_commits_a_lease_fenced_claim():
    conn = FakeConnection(next_job=job_row())
    queue = PostgresEngineeringBuildQueue(lambda: conn)
    claimed = queue.claim_next(worker_id="worker-a", lease_seconds=90)
    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.lease_owner == "worker-a"
    assert claimed.lease_token
    assert any("FOR UPDATE SKIP LOCKED" in sql for sql, _ in conn.statements)
    assert conn.committed and conn.closed


def test_completion_fails_closed_when_database_returns_no_fenced_row():
    conn = FakeConnection(next_job=job_row("running"), return_updated=False)
    queue = PostgresEngineeringBuildQueue(lambda: conn)
    job = EngineeringBuildJob(**{
        **job_row("running"),
        "created_at": job_row()["created_at"].isoformat(),
        "updated_at": job_row()["updated_at"].isoformat(),
        "lease_owner": "worker-a", "lease_token": "token-a",
        "lease_expires_at": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
    })
    with pytest.raises(Phase163Error, match="lease_fence_rejected_or_expired"):
        queue.complete(job, worker_id="worker-a", lease_token="stale-token", status="awaiting_review")
    update = next(sql for sql, _ in conn.statements if sql.startswith("UPDATE public.kdo_engineering_build_jobs"))
    assert "tenant_id=%s AND workspace_id=%s" in update
    assert "lease_token=%s" in update and "lease_expires_at>now()" in update
    assert conn.rolled_back and conn.closed


def test_bootstrap_readiness_is_fail_closed_and_never_claims_release_authority():
    class NotKernel:
        pass
    queue = PostgresEngineeringBuildQueue(lambda: FakeConnection())
    result = verify_shared_queue_configuration(queue=queue, action_kernel=NotKernel(), receipt_verifier=None)
    assert result["ready"] is False
    assert result["release_authorized"] is False
    assert result["checks"]["postgres_queue_adapter"] is True
    assert result["checks"]["receipt_verifier_configured"] is False


def test_migration_enables_rls_and_restricts_browser_roles():
    root = Path(__file__).resolve().parents[2]
    sql = (root / "migrations" / "140_kdo_engineering_shared_build_queue.sql").read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON TABLE public.kdo_engineering_build_jobs FROM PUBLIC, anon, authenticated" in sql
    assert "UNIQUE (tenant_id, workspace_id, idempotency_key)" in sql
    assert "UNIQUE (tenant_id, workspace_id, task_id)" in sql
    assert "lease_expires_at" in sql and "status = 'running'" in sql


def test_shared_queue_does_not_silently_fall_back_to_sqlite():
    queue = PostgresEngineeringBuildQueue(lambda: (_ for _ in ()).throw(RuntimeError("no database")))
    with pytest.raises(Phase164Error, match="operation_failed"):
        queue.get("missing", tenant_id="tenant-a", workspace_id="workspace-a")


def test_postgres_contract_registration_is_explicit_and_high_risk():
    from services.action_kernel import ActionKernel
    from services.kdo_engineering_phase16_4 import register_postgres_build_enqueue_contract

    kernel = ActionKernel(allow_in_memory=True)
    queue = PostgresEngineeringBuildQueue(lambda: FakeConnection())
    register_postgres_build_enqueue_contract(kernel, queue)
    metadata = kernel.get_contract_metadata("kdo_engineering_build_enqueue")
    assert metadata["risk_level"] == "high"
    assert metadata["requires_approval"] is True
    with pytest.raises(Phase164Error, match="already_registered"):
        register_postgres_build_enqueue_contract(kernel, queue)
    ready = verify_shared_queue_configuration(queue=queue, action_kernel=kernel,
                                               receipt_verifier=lambda _scope, _receipt: True)
    assert ready["ready"] is True
    assert ready["release_authorized"] is False


def test_scoped_claim_filters_tenant_and_workspace_in_locked_select():
    conn = FakeConnection(next_job=job_row())
    queue = PostgresEngineeringBuildQueue(lambda: conn)
    claimed = queue.claim_next(worker_id="worker-a", lease_seconds=90,
                               tenant_id="tenant-a", workspace_id="workspace-a")
    assert claimed is not None
    sql, params = next((sql, params) for sql, params in conn.statements
                       if "FOR UPDATE SKIP LOCKED" in sql)
    assert "tenant_id=%s AND workspace_id=%s" in sql
    assert "ORDER BY created_at,job_id LIMIT 1 FOR UPDATE SKIP LOCKED" in sql
    assert params == ("tenant-a", "workspace-a")
    assert conn.committed and conn.closed


@pytest.mark.parametrize("tenant_id,workspace_id", [
    ("tenant-a", None), (None, "workspace-a"), ("", "workspace-a"), ("tenant-a", ""),
])
def test_postgres_scoped_claim_rejects_partial_or_empty_scope(tenant_id, workspace_id):
    queue = PostgresEngineeringBuildQueue(lambda: FakeConnection(next_job=job_row()))
    with pytest.raises(Phase163Error, match="scope"):
        queue.claim_next(worker_id="worker-a", tenant_id=tenant_id, workspace_id=workspace_id)


@pytest.mark.parametrize("lease_seconds", [True, 29, 3601, 30.5, "60"])
def test_postgres_claim_rejects_invalid_lease_duration(lease_seconds):
    queue = PostgresEngineeringBuildQueue(lambda: FakeConnection(next_job=job_row()))
    with pytest.raises(Phase163Error, match="lease_seconds_must_be_between_30_and_3600"):
        queue.claim_next(worker_id="worker-a", lease_seconds=lease_seconds)


def test_postgres_lease_mutations_reject_missing_worker_token_and_invalid_duration():
    queue = PostgresEngineeringBuildQueue(lambda: FakeConnection(next_job=job_row("running")))
    now = datetime.now(timezone.utc)
    job = EngineeringBuildJob(**{
        **job_row("running"),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "lease_owner": "worker-a",
        "lease_token": "token-a",
        "lease_expires_at": (now + timedelta(seconds=60)).isoformat(),
    })
    with pytest.raises(Phase163Error, match="worker_identity_and_lease_token_required"):
        queue.renew(job, worker_id="", lease_token="token-a")
    with pytest.raises(Phase163Error, match="worker_identity_and_lease_token_required"):
        queue.complete(job, worker_id="worker-a", lease_token="", status="awaiting_review")
    with pytest.raises(Phase163Error, match="lease_seconds_must_be_between_30_and_3600"):
        queue.renew(job, worker_id="worker-a", lease_token="token-a", lease_seconds=True)

def test_postgres_renew_rejects_invalid_lease_duration_and_credentials():
    queue = PostgresEngineeringBuildQueue(
        lambda: (_ for _ in ()).throw(AssertionError("DB must not be opened"))
    )
    row = job_row("running")
    row["lease_owner"] = "worker-a"
    row["lease_token"] = "token-a"
    row["lease_expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    job = EngineeringBuildJob(**{
        **row,
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    })

    for invalid in (True, 29, 3601, 30.5, "60"):
        with pytest.raises(Phase163Error, match="lease_seconds_must_be_between_30_and_3600"):
            queue.renew(job, worker_id="worker-a", lease_token="token-a", lease_seconds=invalid)
    with pytest.raises(Phase163Error, match="worker_identity_and_lease_token_required"):
        queue.renew(job, worker_id="", lease_token="token-a", lease_seconds=60)
    with pytest.raises(Phase163Error, match="worker_identity_and_lease_token_required"):
        queue.renew(job, worker_id="worker-a", lease_token="", lease_seconds=60)


def test_postgres_complete_rejects_missing_worker_or_lease_token_before_database_access():
    queue = PostgresEngineeringBuildQueue(
        lambda: (_ for _ in ()).throw(AssertionError("DB must not be opened"))
    )
    row = job_row("running")
    row["lease_owner"] = "worker-a"
    row["lease_token"] = "token-a"
    row["lease_expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    job = EngineeringBuildJob(**{
        **row,
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    })

    with pytest.raises(Phase163Error, match="worker_identity_and_lease_token_required"):
        queue.complete(job, worker_id="", lease_token="token-a", status="awaiting_review")
    with pytest.raises(Phase163Error, match="worker_identity_and_lease_token_required"):
        queue.complete(job, worker_id="worker-a", lease_token="", status="awaiting_review")
        main



def test_reconciliation_audit_migration_is_scoped_and_application_append_only():
    root = Path(__file__).resolve().parents[2]
    sql = (root / "migrations" / "152_kdo_engineering_job_reconciliation_audit.sql").read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON TABLE public.kdo_engineering_job_reconciliation_events" in sql
    assert "GRANT SELECT, INSERT ON TABLE public.kdo_engineering_job_reconciliation_events" in sql
    assert "REVOKE UPDATE, DELETE ON TABLE public.kdo_engineering_job_reconciliation_events" in sql
    assert "worker_death_confirmed" in sql and "replay_authorized" in sql
    assert "evidence_digest" in sql and "approval_ref" in sql
