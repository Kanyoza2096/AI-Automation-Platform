"""Opt-in integration tests against a dedicated, migrated PostgreSQL database.

Set KDO_ENGINEERING_TEST_POSTGRES_DSN to a disposable PostgreSQL/Supabase
service-role DSN. The migration must already be applied. Tests use unique
tenant/workspace identifiers and delete only their own rows during teardown.
They never reset or truncate the shared queue table.
"""
from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

DSN = os.getenv("KDO_ENGINEERING_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="set KDO_ENGINEERING_TEST_POSTGRES_DSN for real PostgreSQL integration tests",
)

psycopg = pytest.importorskip("psycopg")

from services.kdo_engineering_phase16_3 import Phase163Error
from services.kdo_engineering_phase16_4 import PostgresEngineeringBuildQueue

DIGEST = "b" * 64


@pytest.fixture
def postgres_queue_scope():
    tenant_id = "it-tenant-" + uuid.uuid4().hex
    workspace_id = "it-workspace-" + uuid.uuid4().hex

    def connect():
        return psycopg.connect(DSN)

    with connect() as connection:
        row = connection.execute(
            "SELECT to_regclass('public.kdo_engineering_build_jobs')"
        ).fetchone()
        if not row or row[0] is None:
            pytest.fail(
                "Apply migrations/140_kdo_engineering_shared_build_queue.sql "
                "to the dedicated integration database before running this suite"
            )

    queue = PostgresEngineeringBuildQueue(connect)

    yield queue, tenant_id, workspace_id, connect

    # Scope-limited cleanup only; audit rows are removed first in this
    # disposable integration database to satisfy the job foreign key.
    with connect() as connection:
        connection.execute(
            "DELETE FROM public.kdo_engineering_job_reconciliation_events "
            "WHERE tenant_id=%s AND workspace_id=%s",
            (tenant_id, workspace_id),
        )
        connection.execute(
            "DELETE FROM public.kdo_engineering_build_jobs "
            "WHERE tenant_id=%s AND workspace_id=%s",
            (tenant_id, workspace_id),
        )


def enqueue(queue, tenant_id, workspace_id, task_id):
    return queue.enqueue(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        task_id=task_id,
        plan_digest=DIGEST,
        idempotency_key="idem-" + task_id,
    )


def expire_job(connect, job_id):
    with connect() as connection:
        connection.execute(
            "UPDATE public.kdo_engineering_build_jobs "
            "SET lease_expires_at=now()-interval '5 seconds' "
            "WHERE job_id=%s AND status='running'",
            (job_id,),
        )


def test_real_postgres_concurrent_claims_are_unique_and_scope_bound(postgres_queue_scope):
    queue, tenant_id, workspace_id, _connect = postgres_queue_scope
    expected = {
        enqueue(queue, tenant_id, workspace_id, f"task-{index}").job_id
        for index in range(8)
    }

    def claim(index):
        return queue.claim_next(
            worker_id=f"worker-{index}",
            lease_seconds=60,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(claim, range(8)))

    claimed = [job for job in claims if job is not None]
    assert len(claimed) == 8
    assert {job.job_id for job in claimed} == expected
    assert len({job.lease_token for job in claimed}) == 8
    assert all(job.tenant_id == tenant_id for job in claimed)
    assert all(job.workspace_id == workspace_id for job in claimed)


def test_real_postgres_scope_filter_does_not_claim_another_tenant(postgres_queue_scope):
    queue, tenant_id, workspace_id, _connect = postgres_queue_scope
    other_tenant = tenant_id + "-other"
    other_workspace = workspace_id + "-other"
    foreign = enqueue(queue, other_tenant, other_workspace, "foreign-task")

    try:
        claimed = queue.claim_next(
            worker_id="scoped-worker",
            lease_seconds=60,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )

        assert claimed is None
        assert queue.get(
            foreign.job_id, tenant_id=other_tenant, workspace_id=other_workspace
        ).status == "queued"
    finally:
        # The foreign fixture row is outside the primary fixture scope; clean
        # its exact tenant/workspace pair even if an assertion fails.
        with _connect() as connection:
            connection.execute(
                "DELETE FROM public.kdo_engineering_build_jobs "
                "WHERE tenant_id=%s AND workspace_id=%s",
                (other_tenant, other_workspace),
            )


def test_real_postgres_lease_expiry_fences_renewal_and_completion(postgres_queue_scope):
    queue, tenant_id, workspace_id, connect = postgres_queue_scope
    queued = enqueue(queue, tenant_id, workspace_id, "expiry-task")
    claimed = queue.claim_next(
        worker_id="worker-expiry",
        lease_seconds=60,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    assert claimed and claimed.job_id == queued.job_id
    expire_job(connect, claimed.job_id)

    with pytest.raises(Phase163Error, match="lease_fence_rejected_or_expired"):
        queue.renew(
            claimed,
            worker_id="worker-expiry",
            lease_token=claimed.lease_token,
            lease_seconds=60,
        )
    with pytest.raises(Phase163Error, match="lease_fence_rejected_or_expired"):
        queue.complete(
            claimed,
            worker_id="worker-expiry",
            lease_token=claimed.lease_token,
            status="awaiting_review",
        )


def test_real_postgres_expired_dead_worker_becomes_uncertain(postgres_queue_scope):
    queue, tenant_id, workspace_id, connect = postgres_queue_scope
    queued = enqueue(queue, tenant_id, workspace_id, "reconcile-task")
    claimed = queue.claim_next(
        worker_id="worker-gone",
        lease_seconds=60,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    assert claimed and claimed.job_id == queued.job_id
    expire_job(connect, claimed.job_id)

    reconciled = queue.reconcile_expired(
        worker_is_alive=lambda job: False,
        limit=10,
    )

    assert [job.job_id for job in reconciled] == [claimed.job_id]
    assert reconciled[0].status == "uncertain"
    assert reconciled[0].lease_owner is None
    assert reconciled[0].lease_token is None



def test_real_postgres_reconciliation_writes_audit_and_never_marks_unknown_worker_dead(postgres_queue_scope):
    queue, tenant_id, workspace_id, connect = postgres_queue_scope
    queued = enqueue(queue, tenant_id, workspace_id, "audit-reconcile-task")
    claimed = queue.claim_next(
        worker_id="worker-audit", lease_seconds=60,
        tenant_id=tenant_id, workspace_id=workspace_id,
    )
    assert claimed and claimed.job_id == queued.job_id
    expire_job(connect, claimed.job_id)

    # An unavailable worker control plane is not proof of death.
    assert queue.reconcile_expired(
        worker_is_alive=lambda _job: (_ for _ in ()).throw(RuntimeError("control plane down")),
        tenant_id=tenant_id, workspace_id=workspace_id,
    ) == []
    assert queue.get(claimed.job_id, tenant_id=tenant_id, workspace_id=workspace_id).status == "running"

    reconciled = queue.reconcile_expired(
        worker_is_alive=lambda _job: False,
        tenant_id=tenant_id, workspace_id=workspace_id,
    )
    assert [job.job_id for job in reconciled] == [claimed.job_id]
    with connect() as connection:
        event = connection.execute(
            """SELECT event_type,from_status,to_status,actor_id,approval_ref,evidence_digest
               FROM public.kdo_engineering_job_reconciliation_events
               WHERE job_id=%s AND tenant_id=%s AND workspace_id=%s""",
            (claimed.job_id, tenant_id, workspace_id),
        ).fetchone()
    assert event == ("worker_death_confirmed", "running", "uncertain",
                     "system:reconciler", None, None)


def test_real_postgres_uncertain_replay_requires_human_approval_and_records_evidence(postgres_queue_scope):
    queue, tenant_id, workspace_id, connect = postgres_queue_scope
    queued = enqueue(queue, tenant_id, workspace_id, "authorized-replay-task")
    claimed = queue.claim_next(
        worker_id="worker-replay", lease_seconds=60,
        tenant_id=tenant_id, workspace_id=workspace_id,
    )
    assert claimed and claimed.job_id == queued.job_id
    expire_job(connect, claimed.job_id)
    queue.reconcile_expired(worker_is_alive=lambda _job: False,
                            tenant_id=tenant_id, workspace_id=workspace_id)

    with pytest.raises(Phase163Error, match="replay_authorization_denied"):
        queue.authorize_uncertain_replay(
            claimed.job_id, tenant_id=tenant_id, workspace_id=workspace_id,
            actor_id="reviewer-1", approval_ref="approval-123",
            evidence_digest="c" * 64, authorization_verifier=lambda **_: False,
        )

    verified = {}
    def verifier(**claims):
        verified.update(claims)
        return True

    replayed = queue.authorize_uncertain_replay(
        claimed.job_id, tenant_id=tenant_id, workspace_id=workspace_id,
        actor_id="reviewer-1", approval_ref="approval-123",
        evidence_digest="c" * 64, authorization_verifier=verifier,
    )
    assert replayed.status == "queued"
    assert replayed.attempts == claimed.attempts
    assert verified["action"] == "kdo_engineering_uncertain_job_replay"
    assert verified["evidence_digest"] == "c" * 64
    with connect() as connection:
        events = connection.execute(
            """SELECT event_type,from_status,to_status,actor_id,approval_ref,evidence_digest
               FROM public.kdo_engineering_job_reconciliation_events
               WHERE job_id=%s AND tenant_id=%s AND workspace_id=%s
               ORDER BY created_at,event_id""",
            (claimed.job_id, tenant_id, workspace_id),
        ).fetchall()
    assert events == [
        ("worker_death_confirmed", "running", "uncertain", "system:reconciler", None, None),
        ("replay_authorized", "uncertain", "queued", "reviewer-1", "approval-123", "c" * 64),
    ]
