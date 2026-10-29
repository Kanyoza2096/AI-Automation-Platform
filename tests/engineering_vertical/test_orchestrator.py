from pathlib import Path
import os
import subprocess

import pytest
from services.kdo_engineering_orchestrator import run_engineering_task, OrchestrationError


def git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "KDO Test",
        "GIT_AUTHOR_EMAIL": "kdo-test@example.invalid",
        "GIT_COMMITTER_NAME": "KDO Test",
        "GIT_COMMITTER_EMAIL": "kdo-test@example.invalid",
    })
    proc = subprocess.run(["git", *args], cwd=repo, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, check=True, shell=False)
    return proc.stdout


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "hello.py").write_text("print('before')\n", encoding="utf-8")
    git(repo, "add", "hello.py")
    git(repo, "commit", "-qm", "initial")
    return repo


def patch_text():
    return (
        "diff --git a/hello.py b/hello.py\n"
        "--- a/hello.py\n"
        "+++ b/hello.py\n"
        "@@ -1 +1 @@\n"
        "-print('before')\n"
        "+print('after')\n"
    )


def task():
    return {"task_id": "task-1", "workspace_id": "workspace-1", "tenant_id": "tenant-1"}


def test_authorization_denial_blocks_patch(tmp_path):
    repo = make_repo(tmp_path)
    calls = []
    result = run_engineering_task(
        task=task(), repository_path=repo, patch_text=patch_text(),
        test_command=["python", "-c", "print('test')"],
        authorize=lambda action, task: calls.append(action) or False,
    )
    assert result.status == "blocked"
    assert calls == ["apply_patch"]
    assert (repo / "hello.py").read_text() == "print('before')\n"


def test_patch_and_tests_pass_but_run_awaits_review(tmp_path):
    repo = make_repo(tmp_path)
    result = run_engineering_task(
        task=task(), repository_path=repo, patch_text=patch_text(),
        test_command=["python", "-c", "print('test evidence')"],
        authorize=lambda action, task: True,
    )
    assert result.status == "awaiting_review"
    assert result.test_exit_code == 0
    assert result.revision_before == result.revision_after
    assert result.evidence["authorization_checks"] == [
        {"action": "apply_patch", "allowed": True},
        {"action": "run_tests", "allowed": True},
    ]
    assert "final_diff" in result.evidence


def test_test_authorization_denial_does_not_run_tests(tmp_path):
    repo = make_repo(tmp_path)
    calls = []

    def authorize(action, task):
        calls.append(action)
        return action != "run_tests"

    result = run_engineering_task(
        task=task(), repository_path=repo, patch_text=patch_text(),
        test_command=["python", "-c", "raise SystemExit(0)"],
        authorize=authorize,
    )
    assert result.status == "awaiting_approval"
    assert calls == ["apply_patch", "run_tests"]
    assert result.test_exit_code is None


def test_failed_tests_never_await_review_as_success(tmp_path):
    repo = make_repo(tmp_path)
    result = run_engineering_task(
        task=task(), repository_path=repo, patch_text=patch_text(),
        test_command=["python", "-c", "raise SystemExit(7)"],
        authorize=lambda action, task: True,
    )
    assert result.status == "failed"
    assert result.test_exit_code == 7


def test_missing_task_scope_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(OrchestrationError, match="workspace_id"):
        run_engineering_task(
            task={"task_id": "task-1"}, repository_path=repo, patch_text=patch_text(),
            test_command=["python", "-c", "print('x')"],
            authorize=lambda action, task: True,
        )
