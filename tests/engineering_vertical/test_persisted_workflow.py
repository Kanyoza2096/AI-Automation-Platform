from pathlib import Path
import os
import subprocess

import pytest

from services.kdo_engineering_artifacts import SQLiteEngineeringArtifactRepository
from services.kdo_engineering_persisted_workflow import run_persisted_engineering_task
from services.kdo_engineering_task_repository import SQLiteEngineeringTaskRepository
from services.kdo_engineering_vertical_contracts import (
    EngineeringTaskContract,
    EngineeringTaskStatus,
)


def git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "KDO Test",
        "GIT_AUTHOR_EMAIL": "kdo-test@example.invalid",
        "GIT_COMMITTER_NAME": "KDO Test",
        "GIT_COMMITTER_EMAIL": "kdo-test@example.invalid",
    })
    return subprocess.run(["git", *args], cwd=repo, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, check=True, shell=False).stdout


def make_repo(path: Path) -> Path:
    repo = path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "hello.py").write_text("print('before')\n", encoding="utf-8")
    git(repo, "add", "hello.py")
    git(repo, "commit", "-qm", "initial")
    return repo


def make_task(status=EngineeringTaskStatus.PLANNED):
    return EngineeringTaskContract(
        task_id="task-persisted-1", tenant_id="tenant-a", workspace_id="workspace-a",
        objective="Update greeting", acceptance_criteria=("updated greeting",),
        idempotency_key="idem-1", status=status,
    )


def patch():
    return (
        "diff --git a/hello.py b/hello.py\n"
        "--- a/hello.py\n"
        "+++ b/hello.py\n"
        "@@ -1 +1 @@\n"
        "-print('before')\n"
        "+print('after')\n"
    )


def test_persisted_workflow_transitions_and_stores_artifacts(tmp_path):
    tasks = SQLiteEngineeringTaskRepository()
    artifacts = SQLiteEngineeringArtifactRepository()
    task = make_task()
    tasks.create(task)
    repo = make_repo(tmp_path)

    result = run_persisted_engineering_task(
        task_contract=task, task_repository=tasks, artifact_repository=artifacts,
        repository_path=repo, patch_text=patch(),
        test_command=["python", "-c", "print('ok')"],
        authorize=lambda action, payload: True,
    )

    stored = tasks.get(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id)
    assert result.status == "awaiting_review"
    assert stored.status == EngineeringTaskStatus.AWAITING_REVIEW
    assert stored.revision == 4  # planned -> queued -> running -> awaiting_review
    rows = artifacts.list_task(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id)
    assert {x.kind for x in rows} == {"repository_snapshot", "patch", "test_log", "review_report"}
    snapshot = next(x for x in rows if x.kind == "repository_snapshot")
    manifest, content = artifacts.get(snapshot.artifact_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id)
    assert manifest.sha256
    assert b'"revision"' in content


def test_persisted_workflow_rejects_wrong_scope(tmp_path):
    tasks = SQLiteEngineeringTaskRepository()
    artifacts = SQLiteEngineeringArtifactRepository()
    task = make_task()
    tasks.create(task)
    wrong_scope = EngineeringTaskContract(
        task_id=task.task_id, tenant_id="other-tenant", workspace_id=task.workspace_id,
        objective=task.objective, acceptance_criteria=task.acceptance_criteria,
        idempotency_key="other-idem", status=task.status,
    )
    with pytest.raises(Exception):
        run_persisted_engineering_task(
            task_contract=wrong_scope, task_repository=tasks, artifact_repository=artifacts,
            repository_path=make_repo(tmp_path), patch_text=patch(),
            test_command=["python", "-c", "print('ok')"], authorize=lambda a, t: True,
        )


def test_persisted_workflow_does_not_complete_on_test_failure(tmp_path):
    tasks = SQLiteEngineeringTaskRepository()
    artifacts = SQLiteEngineeringArtifactRepository()
    task = make_task(EngineeringTaskStatus.QUEUED)
    tasks.create(task)
    result = run_persisted_engineering_task(
        task_contract=task, task_repository=tasks, artifact_repository=artifacts,
        repository_path=make_repo(tmp_path), patch_text=patch(),
        test_command=["python", "-c", "raise SystemExit(9)"],
        authorize=lambda action, payload: True,
    )
    stored = tasks.get(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id)
    assert result.status == "failed"
    assert stored.status == EngineeringTaskStatus.FAILED



def test_recovery_blocks_only_confirmed_orphaned_running_tasks():
    from services.kdo_engineering_persisted_workflow import recover_interrupted_engineering_tasks

    tasks = SQLiteEngineeringTaskRepository()
    task = make_task(EngineeringTaskStatus.QUEUED)
    tasks.create(task)
    tasks.transition(task.task_id, EngineeringTaskStatus.RUNNING,
                     tenant_id=task.tenant_id, workspace_id=task.workspace_id,
                     expected_revision=task.revision)

    assert recover_interrupted_engineering_tasks(
        task_repository=tasks, tenant_id=task.tenant_id, workspace_id=task.workspace_id,
        worker_is_alive=lambda _: (_ for _ in ()).throw(RuntimeError("probe unavailable")),
    ) == []
    assert tasks.get(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id).status == EngineeringTaskStatus.RUNNING

    recovered = recover_interrupted_engineering_tasks(
        task_repository=tasks, tenant_id=task.tenant_id, workspace_id=task.workspace_id,
        worker_is_alive=lambda _: False,
    )
    assert len(recovered) == 1
    assert recovered[0].status == EngineeringTaskStatus.BLOCKED
    assert recovered[0].evidence["recovery"]["automatic_resume"] is False
    assert recovered[0].evidence["recovery"]["cleanup_required"] is True


def test_recovery_does_not_touch_live_workers():
    from services.kdo_engineering_persisted_workflow import recover_interrupted_engineering_tasks

    tasks = SQLiteEngineeringTaskRepository()
    task = make_task(EngineeringTaskStatus.QUEUED)
    tasks.create(task)
    tasks.transition(task.task_id, EngineeringTaskStatus.RUNNING,
                     tenant_id=task.tenant_id, workspace_id=task.workspace_id,
                     expected_revision=task.revision)
    assert recover_interrupted_engineering_tasks(
        task_repository=tasks, tenant_id=task.tenant_id, workspace_id=task.workspace_id,
        worker_is_alive=lambda _: True,
    ) == []



def test_test_authorization_denial_reverses_applied_patch(tmp_path):
    tasks = SQLiteEngineeringTaskRepository()
    artifacts = SQLiteEngineeringArtifactRepository()
    task = make_task(EngineeringTaskStatus.QUEUED)
    tasks.create(task)
    repo = make_repo(tmp_path)
    result = run_persisted_engineering_task(
        task_contract=task, task_repository=tasks, artifact_repository=artifacts,
        repository_path=repo, patch_text=patch(),
        test_command=["python", "-c", "print('should not run')"],
        authorize=lambda action, payload: action != "run_tests",
    )
    assert result.status == "awaiting_approval"
    assert (repo / "hello.py").read_text(encoding="utf-8") == "print('before')\n"
    assert result.evidence["cleanup"]["status"] == "passed"
    stored = tasks.get(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id)
    assert stored.status == EngineeringTaskStatus.BLOCKED
