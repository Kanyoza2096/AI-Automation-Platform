from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.action_kernel import ActionKernel
from services.kdo_engineering_phase16_3 import (
    Phase163Error, SQLiteEngineeringBuildQueue,
    register_engineering_build_enqueue_contract,
)

DIGEST = "a" * 64


def make_queue(tmp_path):
    return SQLiteEngineeringBuildQueue(tmp_path / "build-jobs.sqlite")


def test_queue_is_idempotent_and_rejects_conflicting_payload(tmp_path):
    queue = make_queue(tmp_path)
    first = queue.enqueue(tenant_id="t1", workspace_id="w1", task_id="task1",
                          plan_digest=DIGEST, idempotency_key="idem1")
    repeated = queue.enqueue(tenant_id="t1", workspace_id="w1", task_id="task1",
                             plan_digest=DIGEST, idempotency_key="idem1")
    assert first.job_id == repeated.job_id
    with pytest.raises(Phase163Error, match="payload_conflict"):
        queue.enqueue(tenant_id="t1", workspace_id="w1", task_id="task1",
                      plan_digest="b" * 64, idempotency_key="idem1")
    queue.close()


def test_claim_lease_is_fenced_and_second_worker_cannot_claim_same_job(tmp_path):
    queue = make_queue(tmp_path)
    queue.enqueue(tenant_id="t1", workspace_id="w1", task_id="task1",
                  plan_digest=DIGEST, idempotency_key="idem1")
    claimed = queue.claim_next(worker_id="worker-a", lease_seconds=60)
    assert claimed and claimed.status == "running" and claimed.lease_token
    assert queue.claim_next(worker_id="worker-b", lease_seconds=60) is None
    with pytest.raises(Phase163Error, match="lease_fence_rejected"):
        queue.complete(claimed, worker_id="worker-b", lease_token=claimed.lease_token, status="awaiting_review")
    done = queue.complete(claimed, worker_id="worker-a", lease_token=claimed.lease_token, status="awaiting_review")
    assert done.status == "awaiting_review"
    queue.close()


def test_expired_lease_is_uncertain_only_when_worker_absence_confirmed(tmp_path):
    queue = make_queue(tmp_path)
    queue.enqueue(tenant_id="t1", workspace_id="w1", task_id="task1",
                  plan_digest=DIGEST, idempotency_key="idem1")
    claimed = queue.claim_next(worker_id="worker-a", lease_seconds=30)
    expired = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    with queue._lock:
        queue._db.execute("UPDATE kdo_engineering_build_jobs SET lease_expires_at=? WHERE job_id=?", (expired, claimed.job_id))
    assert queue.reconcile_expired(worker_is_alive=lambda _: True) == []
    assert queue.reconcile_expired(worker_is_alive=lambda _: (_ for _ in ()).throw(RuntimeError("probe"))) == []
    recovered = queue.reconcile_expired(worker_is_alive=lambda _: False)
    assert len(recovered) == 1
    assert recovered[0].status == "uncertain"
    assert queue.claim_next(worker_id="worker-b") is None  # no automatic replay
    queue.close()


def test_queue_metadata_is_tenant_scoped(tmp_path):
    queue = make_queue(tmp_path)
    job = queue.enqueue(tenant_id="tenant-a", workspace_id="workspace-a", task_id="task-a",
                        plan_digest=DIGEST, idempotency_key="idem-a")
    with pytest.raises(Phase163Error, match="not_found"):
        queue.get(job.job_id, tenant_id="tenant-b", workspace_id="workspace-a")
    queue.close()


def test_queue_persists_across_repository_reopen(tmp_path):
    queue = make_queue(tmp_path)
    job = queue.enqueue(tenant_id="t1", workspace_id="w1", task_id="task1",
                        plan_digest=DIGEST, idempotency_key="idem1")
    queue.close()
    reopened = make_queue(tmp_path)
    assert reopened.get(job.job_id, tenant_id="t1", workspace_id="w1").status == "queued"
    reopened.close()


def test_enqueue_contract_is_high_risk_and_requires_explicit_decision(tmp_path):
    queue = make_queue(tmp_path)
    kernel = ActionKernel(allow_in_memory=True)
    register_engineering_build_enqueue_contract(kernel, queue)
    metadata = kernel.get_contract_metadata("kdo_engineering_build_enqueue")
    assert metadata["risk_level"] == "high"
    assert metadata["requires_approval"] is True
    assert metadata["inline_with_decision"] is True
    import asyncio
    result = asyncio.run(kernel.execute(workspace_id="w1", action_type="kdo_engineering_build_enqueue",
        risk_level="high", parameters={"tenant_id": "t1", "workspace_id": "w1", "task_id": "task1",
                    "plan_digest": DIGEST, "job_idempotency_key": "idem1"}))
    assert result["status"] == "governance_required"
    assert queue.claim_next(worker_id="worker") is None
    queue.close()


def test_contract_registration_is_explicit_and_duplicate_registration_fails(tmp_path):
    queue = make_queue(tmp_path)
    kernel = ActionKernel(allow_in_memory=True)
    register_engineering_build_enqueue_contract(kernel, queue)
    with pytest.raises(Phase163Error, match="already_registered"):
        register_engineering_build_enqueue_contract(kernel, queue)
    queue.close()


def _make_approved_workflow(tmp_path):
    import os
    import subprocess
    from services.kdo_engineering_phase16_2 import KDOPhase162Workflow, SQLiteEngineeringWorkspaceRepository
    from services.kdo_engineering_task_repository import SQLiteEngineeringTaskRepository
    from services.kdo_engineering_vertical_contracts import EngineeringTaskContract, EngineeringTaskStatus

    source = tmp_path / "source-repo"
    source.mkdir()
    env = dict(os.environ)
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "KDO Test",
                "GIT_AUTHOR_EMAIL": "kdo-test@example.invalid", "GIT_COMMITTER_NAME": "KDO Test",
                "GIT_COMMITTER_EMAIL": "kdo-test@example.invalid"})
    def git(*args):
        return subprocess.run(["git", *args], cwd=source, env=env, check=True, shell=False,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()
    git("init", "-q")
    (source / "app.py").write_text("print('base')\n", encoding="utf-8")
    git("add", "app.py")
    git("commit", "-qm", "base")
    tasks = SQLiteEngineeringTaskRepository(tmp_path / "tasks.sqlite")
    workspaces = SQLiteEngineeringWorkspaceRepository(tmp_path / "workspaces.sqlite")
    permissions = {"creator": {"create_task", "submit_plan"}, "reviewer": {"review_plan"},
                   "builder": {"enqueue_build", "execute_build_worker", "execute_build"}}
    workflow = KDOPhase162Workflow(task_repository=tasks, workspace_repository=workspaces,
        source_repository_path=source, managed_root=tmp_path / "managed",
        authorize=lambda actor, action, tenant, workspace: action in permissions.get(actor, set()))
    task = EngineeringTaskContract(task_id="task-1", tenant_id="tenant-a", workspace_id="workspace-a",
        objective="Change app greeting", acceptance_criteria=("app.py changed",), idempotency_key="idem-1",
        status=EngineeringTaskStatus.PROPOSED, base_revision="HEAD")
    workflow.create_task(task, actor_id="creator")
    workflow.submit_plan(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id,
                         actor_id="creator", plan=["Edit app.py", "Run required checks"])
    workflow.review_plan(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id,
                         reviewer_id="reviewer", approve=True)
    return workflow, task, source


def test_council_and_action_kernel_admission_creates_durable_job_only_after_approval(tmp_path):
    import asyncio
    from services.kdo_engineering_phase16_3 import admit_approved_build

    workflow, task, _ = _make_approved_workflow(tmp_path)
    queue = make_queue(tmp_path)
    kernel = ActionKernel(allow_in_memory=True)
    register_engineering_build_enqueue_contract(kernel, queue)
    result = asyncio.run(admit_approved_build(workflow=workflow, action_kernel=kernel,
        task_id=task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id,
        actor_id="builder", agent_id="kdo-agent",
        council_deliberate=lambda **kwargs: {"is_approved": True, "decision_id": "council-decision-1"},
        receipt_verifier=lambda scope, receipt: True))
    assert result["ok"] is True
    job = queue.claim_next(worker_id="worker-1")
    assert job is not None and job.status == "running"
    assert job.task_id == task.task_id
    queue.close()


def test_claimed_worker_runs_approved_task_and_finishes_at_review_boundary(tmp_path):
    from services.kdo_engineering_phase16_3 import run_claimed_build_job
    from services.kdo_engineering_vertical_contracts import EngineeringTaskStatus

    workflow, task, source = _make_approved_workflow(tmp_path)
    queue = make_queue(tmp_path)
    job = queue.enqueue(tenant_id=task.tenant_id, workspace_id=task.workspace_id,
        task_id=task.task_id, plan_digest=workflow.tasks.get(task.task_id,
            tenant_id=task.tenant_id, workspace_id=task.workspace_id).evidence["plan_review"]["digest"],
        idempotency_key="approved-build-1")
    claimed = queue.claim_next(worker_id="worker-1")
    assert claimed and claimed.job_id == job.job_id
    def executor(path, _contract, _plan):
        path = Path(path)
        (path / "app.py").write_text("print('changed')\n", encoding="utf-8")
        return {"changed": ["app.py"], "tests": "not-run-by-test-executor"}
    result = run_claimed_build_job(workflow=workflow, queue=queue, job=claimed,
        worker_id="worker-1", lease_token=claimed.lease_token, actor_id="builder", executor=executor)
    assert result["task"].status == EngineeringTaskStatus.AWAITING_REVIEW
    assert queue.get(job.job_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id).status == "awaiting_review"
    assert (source / "app.py").read_text(encoding="utf-8") == "print('base')\n"
    queue.close()


def test_unauthorized_actor_cannot_reach_council_or_enqueue(tmp_path):
    import asyncio
    from services.kdo_engineering_phase16_2 import Phase162Error
    from services.kdo_engineering_phase16_3 import admit_approved_build

    workflow, task, _ = _make_approved_workflow(tmp_path)
    queue = make_queue(tmp_path)
    kernel = ActionKernel(allow_in_memory=True)
    register_engineering_build_enqueue_contract(kernel, queue)
    called = []
    with pytest.raises(Phase162Error, match="unauthorized_enqueue_build"):
        asyncio.run(admit_approved_build(workflow=workflow, action_kernel=kernel,
            task_id=task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id,
            actor_id="intruder", agent_id="kdo-agent",
            council_deliberate=lambda **kwargs: called.append(kwargs) or {"is_approved": True, "decision_id": "fake"},
            receipt_verifier=lambda scope, receipt: True))
    assert called == []
    assert queue.claim_next(worker_id="worker") is None
    queue.close()


def test_claimed_job_cannot_be_relabelled_to_another_task(tmp_path):
    from dataclasses import replace

    queue = make_queue(tmp_path)
    job = queue.enqueue(tenant_id="t1", workspace_id="w1", task_id="task1",
                        plan_digest=DIGEST, idempotency_key="idem1")
    claimed = queue.claim_next(worker_id="worker-a")
    forged = replace(claimed, task_id="task-other")
    with pytest.raises(Phase163Error, match="lease_fence_rejected"):
        queue.complete(forged, worker_id="worker-a", lease_token=claimed.lease_token, status="awaiting_review")
    queue.close()


def test_lease_renewal_extends_expiry_and_preserves_fencing(tmp_path):
    queue = make_queue(tmp_path)
    queue.enqueue(tenant_id="t1", workspace_id="w1", task_id="task1",
                  plan_digest=DIGEST, idempotency_key="idem1")
    claimed = queue.claim_next(worker_id="worker-a", lease_seconds=30)
    assert claimed is not None
    before = datetime.fromisoformat(claimed.lease_expires_at)
    renewed = queue.renew(claimed, worker_id="worker-a", lease_token=claimed.lease_token,
                          lease_seconds=120)
    after = datetime.fromisoformat(renewed.lease_expires_at)
    assert after > before
    assert renewed.lease_token == claimed.lease_token
    with pytest.raises(Phase163Error, match="lease_fence_rejected"):
        queue.renew(renewed, worker_id="worker-b", lease_token=claimed.lease_token)
    queue.close()


def test_expired_lease_cannot_be_renewed_or_completed(tmp_path):
    queue = make_queue(tmp_path)
    queue.enqueue(tenant_id="t1", workspace_id="w1", task_id="task1",
                  plan_digest=DIGEST, idempotency_key="idem1")
    claimed = queue.claim_next(worker_id="worker-a", lease_seconds=30)
    expired = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    with queue._lock:
        queue._db.execute(
            "UPDATE kdo_engineering_build_jobs SET lease_expires_at=? WHERE job_id=?",
            (expired, claimed.job_id),
        )
    with pytest.raises(Phase163Error, match="worker_lease_expired"):
        queue.renew(claimed, worker_id="worker-a", lease_token=claimed.lease_token)
    with pytest.raises(Phase163Error, match="worker_lease_expired_completion_uncertain"):
        queue.complete(claimed, worker_id="worker-a", lease_token=claimed.lease_token,
                       status="awaiting_review")
    queue.close()


@pytest.mark.parametrize("worker_id,lease_token", [("", "token"), ("worker", ""), (None, "token")])
def test_lease_mutations_require_worker_identity_and_token(tmp_path, worker_id, lease_token):
    queue = make_queue(tmp_path)
    queue.enqueue(tenant_id="t1", workspace_id="w1", task_id="task1",
                  plan_digest=DIGEST, idempotency_key="idem1")
    claimed = queue.claim_next(worker_id="worker-a", lease_seconds=30)
    with pytest.raises(Phase163Error, match="worker_identity_and_lease_token_required"):
        queue.renew(claimed, worker_id=worker_id, lease_token=lease_token)
    queue.close()


@pytest.mark.parametrize("lease_seconds", [True, 29, 3601, 30.5, "60"])
def test_claim_rejects_invalid_lease_duration_types_and_bounds(tmp_path, lease_seconds):
    queue = make_queue(tmp_path)
    with pytest.raises(Phase163Error, match="lease_seconds_must_be_between_30_and_3600"):
        queue.claim_next(worker_id="worker-a", lease_seconds=lease_seconds)
    queue.close()


def test_scoped_claim_skips_jobs_outside_tenant_and_workspace(tmp_path):
    queue = make_queue(tmp_path)
    other = queue.enqueue(tenant_id="tenant-b", workspace_id="workspace-b", task_id="other-task",
                          plan_digest=DIGEST, idempotency_key="other-idem")
    expected = queue.enqueue(tenant_id="tenant-a", workspace_id="workspace-a", task_id="target-task",
                             plan_digest=DIGEST, idempotency_key="target-idem")
    claimed = queue.claim_next(worker_id="worker-a", tenant_id="tenant-a", workspace_id="workspace-a")
    assert claimed is not None
    assert claimed.job_id == expected.job_id
    assert claimed.tenant_id == "tenant-a" and claimed.workspace_id == "workspace-a"
    assert queue.get(other.job_id, tenant_id="tenant-b", workspace_id="workspace-b").status == "queued"
    queue.close()


@pytest.mark.parametrize("tenant_id,workspace_id", [
    ("tenant-a", None), (None, "workspace-a"), ("", "workspace-a"), ("tenant-a", ""),
])
def test_scoped_claim_requires_complete_scope(tmp_path, tenant_id, workspace_id):
    queue = make_queue(tmp_path)
    with pytest.raises(Phase163Error, match="scope"):
        queue.claim_next(worker_id="worker-a", tenant_id=tenant_id, workspace_id=workspace_id)
    queue.close()
