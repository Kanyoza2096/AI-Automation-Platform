from pathlib import Path
import subprocess

import pytest

from services.kdo_engineering_workspaces import (
    EngineeringWorkspaceError,
    create_task_worktree,
    inspect_task_worktree,
    remove_task_worktree,
)


def git(repo: Path, *args: str) -> str:
    env = {
        "PATH": __import__("os").environ.get("PATH", ""),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "KDO Test",
        "GIT_AUTHOR_EMAIL": "kdo-test@example.invalid",
        "GIT_COMMITTER_NAME": "KDO Test",
        "GIT_COMMITTER_EMAIL": "kdo-test@example.invalid",
    }
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, shell=False).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    (source / "app.py").write_text("print('hello')\n", encoding="utf-8")
    git(source, "add", "app.py")
    git(source, "commit", "-qm", "initial")
    return source


def test_create_isolated_task_branch_and_worktree(repo: Path, tmp_path: Path):
    managed = tmp_path / "managed"
    result = create_task_worktree(repo, managed, tenant_id="tenant-1",
                                  workspace_id="workspace-1", task_id="task-1")
    path = Path(result.path)
    assert result.status == "created"
    assert path.is_dir()
    assert path != repo
    assert result.branch.startswith("kdo/tenant-1/workspace-1/task-1-")
    assert git(path, "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")
    assert git(path, "branch", "--show-current") == result.branch
    assert git(repo, "status", "--porcelain") == ""


def test_duplicate_task_workspace_is_rejected(repo: Path, tmp_path: Path):
    args = dict(tenant_id="t", workspace_id="w", task_id="same")
    create_task_worktree(repo, tmp_path / "managed", **args)
    with pytest.raises(EngineeringWorkspaceError, match="already_exists"):
        create_task_worktree(repo, tmp_path / "managed", **args)


def test_managed_root_inside_repository_is_rejected(repo: Path, tmp_path: Path):
    with pytest.raises(EngineeringWorkspaceError, match="outside_source"):
        create_task_worktree(repo, repo / "task-worktrees", tenant_id="t",
                             workspace_id="w", task_id="x")


def test_inspection_and_clean_removal(repo: Path, tmp_path: Path):
    managed = tmp_path / "managed"
    result = create_task_worktree(repo, managed, tenant_id="t", workspace_id="w", task_id="x")
    snapshot = inspect_task_worktree(result.path, managed_root=managed)
    assert snapshot["branch"] == result.branch
    assert snapshot["revision"] == result.base_revision
    assert snapshot["diff"] == ""
    remove_task_worktree(result.path, repository_path=repo, managed_root=managed)
    assert not Path(result.path).exists()


def test_dirty_worktree_is_preserved(repo: Path, tmp_path: Path):
    managed = tmp_path / "managed"
    result = create_task_worktree(repo, managed, tenant_id="t", workspace_id="w", task_id="dirty")
    path = Path(result.path)
    (path / "app.py").write_text("print('changed')\n", encoding="utf-8")
    with pytest.raises(EngineeringWorkspaceError, match="dirty_workspace_preserved"):
        remove_task_worktree(result.path, repository_path=repo, managed_root=managed)
    assert path.exists()
    assert "changed" in (path / "app.py").read_text(encoding="utf-8")


def test_path_escape_and_invalid_identity_are_rejected(repo: Path, tmp_path: Path):
    with pytest.raises(EngineeringWorkspaceError, match="task_id_required"):
        create_task_worktree(repo, tmp_path / "managed", tenant_id="t", workspace_id="w", task_id=" ")
    with pytest.raises(EngineeringWorkspaceError, match="outside_managed_root"):
        inspect_task_worktree(repo, managed_root=tmp_path / "managed")
