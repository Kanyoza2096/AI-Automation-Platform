from pathlib import Path
import subprocess

import pytest

from services.kdo_engineering_repository import (
    RepositoryInputError,
    apply_patch,
    inspect_repository,
)


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, check=True, shell=False,
        env={"PATH": __import__("os").environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1",
             "GIT_AUTHOR_NAME": "KDO Test", "GIT_AUTHOR_EMAIL": "kdo-test@example.invalid",
             "GIT_COMMITTER_NAME": "KDO Test", "GIT_COMMITTER_EMAIL": "kdo-test@example.invalid"},
    )
    return proc.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    (tmp_path / "hello.txt").write_text("before\n", encoding="utf-8")
    git(tmp_path, "add", "hello.txt")
    git(tmp_path, "commit", "-qm", "initial")
    return tmp_path


def test_inspect_reports_revision_status_and_diff(repo: Path):
    (repo / "hello.txt").write_text("changed\n", encoding="utf-8")
    result = inspect_repository(repo)
    assert result["status"].status == "passed"
    assert result["revision"].status == "passed"
    assert len(result["revision"].stdout.strip()) == 40
    assert result["diff"].status == "passed"
    assert "-before" in result["diff"].stdout
    assert "+changed" in result["diff"].stdout


def test_inspect_does_not_mutate_repository(repo: Path):
    before = git(repo, "rev-parse", "HEAD").strip()
    inspect_repository(repo)
    after = git(repo, "rev-parse", "HEAD").strip()
    assert before == after


def test_apply_patch_changes_worktree_without_commit(repo: Path):
    patch = (
        "diff --git a/hello.txt b/hello.txt\n"
        "index 3367afd..2e5f3a5 100644\n"
        "--- a/hello.txt\n"
        "+++ b/hello.txt\n"
        "@@ -1 +1 @@\n"
        "-before\n"
        "+after\n"
    )
    before = git(repo, "rev-parse", "HEAD").strip()
    result = apply_patch(repo, patch)
    assert result.status == "passed"
    assert (repo / "hello.txt").read_text(encoding="utf-8") == "after\n"
    assert git(repo, "rev-parse", "HEAD").strip() == before


def test_invalid_patch_fails_without_mutating_worktree(repo: Path):
    original = (repo / "hello.txt").read_text(encoding="utf-8")
    patch = "--- a/hello.txt\n+++ b/hello.txt\n@@ -1 +1 @@\n-missing\n+after\n"
    result = apply_patch(repo, patch)
    assert result.status == "failed"
    assert (repo / "hello.txt").read_text(encoding="utf-8") == original


def test_rejects_non_git_directory(tmp_path: Path):
    with pytest.raises(RepositoryInputError):
        inspect_repository(tmp_path)


def test_rejects_empty_patch(repo: Path):
    with pytest.raises(RepositoryInputError):
        apply_patch(repo, "  ")


def test_rejects_oversized_patch(repo: Path):
    with pytest.raises(RepositoryInputError):
        apply_patch(repo, "x" * (2_000_001))


def test_rejects_bad_timeout(repo: Path):
    with pytest.raises(RepositoryInputError):
        inspect_repository(repo, timeout_seconds=0)
