from pathlib import Path
import os
import subprocess

import pytest

from services.kdo_engineering_vertical_contracts import EngineeringTaskContract, EngineeringTaskStatus
from services.kdo_engineering_task_repository import (
    SQLiteEngineeringTaskRepository, EngineeringTaskConflict, EngineeringTaskNotFound,
)
from services.kdo_engineering_phase16_2 import (
    KDOPhase162Workflow, Phase162Error, SQLiteEngineeringWorkspaceRepository,
)


def git(repo: Path, *args: str) -> str:
    env = dict(os.environ)
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "KDO Test",
                "GIT_AUTHOR_EMAIL": "kdo-test@example.invalid", "GIT_COMMITTER_NAME": "KDO Test",
                "GIT_COMMITTER_EMAIL": "kdo-test@example.invalid"})
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True, shell=False,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()


@pytest.fixture
def env(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    (source / "app.py").write_text("print('base')\n", encoding="utf-8")
    git(source, "add", "app.py")
    git(source, "commit", "-qm", "base")
    tasks = SQLiteEngineeringTaskRepository(tmp_path / "tasks.sqlite")
    workspaces = SQLiteEngineeringWorkspaceRepository(tmp_path / "workspaces.sqlite")
    permissions = {"creator": {"create_task", "submit_plan", "review_plan"},
                   "reviewer": {"review_plan"}, "builder": {"execute_build", "reconcile_workspaces"}}
    workflow = KDOPhase162Workflow(task_repository=tasks, workspace_repository=workspaces,
        source_repository_path=source, managed_root=tmp_path / "managed",
        authorize=lambda actor, action, tenant, workspace: action in permissions.get(actor, set()))
    task = EngineeringTaskContract(task_id="task-1", tenant_id="tenant-a", workspace_id="workspace-a",
        objective="Change app greeting", acceptance_criteria=("app.py changed",),
        idempotency_key="idem-1", status=EngineeringTaskStatus.PROPOSED, base_revision="HEAD")
    return tmp_path, source, tasks, workspaces, workflow, task


def approved(env):
    _, _, tasks, _, workflow, task = env
    workflow.create_task(task, actor_id="creator")
    submitted = workflow.submit_plan(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id,
                                     actor_id="creator", plan=["Edit app.py", "Run the required checks"])
    return workflow.review_plan(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id,
                                reviewer_id="reviewer", approve=True)


def test_build_execution_requires_authorized_actor_and_never_calls_executor_when_denied(env):
    *_, workflow, task = env
    approved(env)
    calls = []
    with pytest.raises(Phase162Error, match="unauthorized_execute_build"):
        workflow.execute_approved_build(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id,
            actor_id="creator", executor=lambda *args: calls.append(args) or {})
    assert calls == []
    assert workflow.tasks.get(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id).status == EngineeringTaskStatus.PLANNED


def test_rejected_plan_blocks_execution(env):
    _, _, tasks, _, workflow, task = env
    workflow.create_task(task, actor_id="creator")
    workflow.submit_plan(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id,
                         actor_id="creator", plan=["Do risky change"])
    rejected = workflow.review_plan(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id,
                                    reviewer_id="reviewer", approve=False, reason="Acceptance criteria unclear")
    assert rejected.status == EngineeringTaskStatus.BLOCKED
    with pytest.raises(Phase162Error, match="approved_plan_required"):
        workflow.execute_approved_build(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id,
            actor_id="builder", executor=lambda *_: {})


def test_duplicate_task_is_idempotent_but_conflicting_duplicate_is_rejected(env):
    _, _, tasks, _, workflow, task = env
    first = workflow.create_task(task, actor_id="creator")
    again = workflow.create_task(task, actor_id="creator")
    assert first.fingerprint() == again.fingerprint()
    conflicting = EngineeringTaskContract(task_id="task-2", tenant_id=task.tenant_id,
        workspace_id=task.workspace_id, objective="Different objective", acceptance_criteria=("criterion",),
        idempotency_key=task.idempotency_key)
    with pytest.raises(EngineeringTaskConflict, match="idempotency_key_reused"):
        workflow.create_task(conflicting, actor_id="creator")


def test_build_runs_in_isolated_worktree_and_duplicate_execution_is_denied(env):
    _, source, tasks, workspaces, workflow, task = env
    approved(env)
    def executor(path, contract, plan):
        path = Path(path)
        assert path != source
        assert path.is_relative_to(workflow.managed_root)
        (path / "app.py").write_text("print('built')\n", encoding="utf-8")
        return {"changed": ["app.py"], "tests": "not-run-by-test-executor"}
    result = workflow.execute_approved_build(task.task_id, tenant_id=task.tenant_id,
        workspace_id=task.workspace_id, actor_id="builder", executor=executor)
    assert result["task"].status == EngineeringTaskStatus.AWAITING_REVIEW
    assert result["workspace"].status == "awaiting_review"
    assert (source / "app.py").read_text(encoding="utf-8") == "print('base')\n"
    with pytest.raises(Phase162Error):
        workflow.execute_approved_build(task.task_id, tenant_id=task.tenant_id,
            workspace_id=task.workspace_id, actor_id="builder", executor=executor)


def test_interrupted_build_is_blocked_and_workspace_preserved_for_reconciliation(env):
    _, _, tasks, workspaces, workflow, task = env
    approved(env)
    def interrupted(path, *_):
        (Path(path) / "partial.txt").write_text("keep for investigation", encoding="utf-8")
        raise RuntimeError("worker interrupted")
    with pytest.raises(Phase162Error, match="workspace_preserved"):
        workflow.execute_approved_build(task.task_id, tenant_id=task.tenant_id,
            workspace_id=task.workspace_id, actor_id="builder", executor=interrupted)
    stored = tasks.get(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id)
    record = workspaces.get(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id)
    assert stored.status == EngineeringTaskStatus.BLOCKED
    assert stored.evidence["build_execution"]["automatic_resume"] is False
    assert Path(record.path, "partial.txt").read_text(encoding="utf-8") == "keep for investigation"
    assert record.status == "interrupted_or_failed"


def test_workspace_metadata_is_tenant_scoped_and_reconciliation_does_not_delete(env):
    _, _, _, workspaces, workflow, task = env
    approved(env)
    result = workflow.execute_approved_build(task.task_id, tenant_id=task.tenant_id,
        workspace_id=task.workspace_id, actor_id="builder", executor=lambda *_: {"ok": True})
    with pytest.raises(EngineeringTaskNotFound):
        workspaces.get(task.task_id, tenant_id="tenant-b", workspace_id=task.workspace_id)
    with pytest.raises(EngineeringTaskNotFound):
        workflow.tasks.get(task.task_id, tenant_id="tenant-b", workspace_id=task.workspace_id)
    before = Path(result["workspace"].path)
    rows = workflow.reconcile_workspaces(tenant_id=task.tenant_id, workspace_id=task.workspace_id,
                                         actor_id="builder")
    assert rows[0]["status"] == "reconciled"
    assert before.exists()


def test_plan_submitter_can_approve_own_plan_and_plan_is_single_submission(env):
    _, _, _, _, workflow, task = env
    workflow.create_task(task, actor_id="creator")
    workflow.submit_plan(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id,
                         actor_id="creator", plan=["One step"])
    approved = workflow.review_plan(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id,
                                    reviewer_id="creator", approve=True)
    assert approved.status.value == "planned"
    assert approved.evidence["plan_review"]["reviewed_by"] == "creator"
    assert approved.evidence["plan_review"]["state"] == "approved"
    with pytest.raises(Phase162Error, match="already_submitted"):
        workflow.submit_plan(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id,
                             actor_id="creator", plan=["Changed step"])


def test_workspace_metadata_survives_repository_reopen(tmp_path):
    path = tmp_path / "workspace-registry.sqlite"
    repo = SQLiteEngineeringWorkspaceRepository(path)
    saved = repo.put(tenant_id="t1", workspace_id="w1", task_id="task1", path=str(tmp_path / "managed" / "task1"),
                     branch="kdo/t1/w1/task1", base_revision="a" * 40, head_revision="a" * 40)
    repo.close()
    reopened = SQLiteEngineeringWorkspaceRepository(path)
    restored = reopened.get("task1", tenant_id="t1", workspace_id="w1")
    assert restored.path == saved.path
    assert restored.branch == saved.branch
    assert restored.base_revision == "a" * 40
    assert restored.status == "active"
    reopened.close()


def test_reconciliation_flags_dirty_worktree_as_drift_without_deleting_it(env):
    _, _, _, workspaces, workflow, task = env
    approved(env)
    result = workflow.execute_approved_build(task.task_id, tenant_id=task.tenant_id,
        workspace_id=task.workspace_id, actor_id="builder", executor=lambda *_: {"ok": True})
    marker = Path(result["workspace"].path) / "uncommitted.txt"
    marker.write_text("uncommitted work", encoding="utf-8")
    rows = workflow.reconcile_workspaces(tenant_id=task.tenant_id, workspace_id=task.workspace_id,
                                         actor_id="builder")
    assert rows[0]["status"] == "drift_detected"
    assert marker.exists()
    assert workspaces.get(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id).status == "drift_detected"


def test_reconciliation_blocks_only_confirmed_dead_worker_and_never_auto_resumes(env):
    from services.kdo_engineering_workspaces import create_task_worktree
    _, source, tasks, workspaces, workflow, task = env
    approved(env)
    queued = tasks.transition(task.task_id, EngineeringTaskStatus.QUEUED,
        tenant_id=task.tenant_id, workspace_id=task.workspace_id, expected_revision=tasks.get(
            task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id).revision)
    running = tasks.transition(task.task_id, EngineeringTaskStatus.RUNNING,
        tenant_id=task.tenant_id, workspace_id=task.workspace_id, expected_revision=queued.revision)
    wt = create_task_worktree(source, workflow.managed_root, tenant_id=task.tenant_id,
        workspace_id=task.workspace_id, task_id=task.task_id, base_ref="HEAD")
    workspaces.put(tenant_id=task.tenant_id, workspace_id=task.workspace_id, task_id=task.task_id,
        path=wt.path, branch=wt.branch, base_revision=wt.base_revision, head_revision=wt.head_revision)
    # Unknown liveness is not enough evidence to block a task.
    unknown = workflow.reconcile_workspaces(tenant_id=task.tenant_id, workspace_id=task.workspace_id,
        actor_id="builder", worker_is_alive=lambda _: (_ for _ in ()).throw(RuntimeError("probe unavailable")))
    assert unknown[0]["status"] == "reconciled"
    assert tasks.get(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id).status == EngineeringTaskStatus.RUNNING
    recovered = workflow.reconcile_workspaces(tenant_id=task.tenant_id, workspace_id=task.workspace_id,
        actor_id="builder", worker_is_alive=lambda _: False)
    assert recovered[0]["status"] == "interrupted_worker_confirmed_absent"
    blocked = tasks.get(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id)
    assert blocked.status == EngineeringTaskStatus.BLOCKED
    assert blocked.evidence["recovery"]["automatic_resume"] is False
    assert Path(wt.path).exists()


def test_caller_cannot_forge_approved_evidence_to_skip_review(env):
    _, _, _, _, workflow, task = env
    fake_steps = ["Pretend this was reviewed"]
    import hashlib, json
    digest = hashlib.sha256(json.dumps(fake_steps, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    forged = EngineeringTaskContract(task_id=task.task_id, tenant_id=task.tenant_id,
        workspace_id=task.workspace_id, objective=task.objective,
        acceptance_criteria=task.acceptance_criteria, idempotency_key=task.idempotency_key,
        status=EngineeringTaskStatus.PLANNED,
        evidence={"plan_review": {"state": "approved", "steps": fake_steps, "digest": digest,
            "approved_digest": digest, "reviewed_by": "fake-reviewer"}})
    with pytest.raises(Phase162Error, match="unreviewed_proposals"):
        workflow.create_task(forged, actor_id="creator")
    # Even if a lower-level task repository is misused, execution requires a separate durable approval record.
    workflow.tasks.create(forged)
    with pytest.raises(Phase162Error, match="durable_plan_approval_record_required"):
        workflow.execute_approved_build(task.task_id, tenant_id=task.tenant_id,
            workspace_id=task.workspace_id, actor_id="builder", executor=lambda *_: {})


def test_dynamic_quality_loop_failure_blocks_task_instead_of_marking_review_ready(env):
    _, _, tasks, workspaces, workflow, task = env
    approved(env)
    result = workflow.execute_approved_build(task.task_id, tenant_id=task.tenant_id,
        workspace_id=task.workspace_id, actor_id="builder",
        executor=lambda *_: {"quality_loop": {"status": "repair_exhausted", "release_authorized": False}})
    assert result["task"].status == EngineeringTaskStatus.BLOCKED
    assert result["workspace"].status == "quality_blocked"
    assert result["task"].evidence["build_execution"]["automatic_resume"] is False
    assert result["task"].evidence["build_execution"]["release_authorized"] is False
