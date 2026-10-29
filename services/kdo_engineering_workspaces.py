"""Bounded Git worktree lifecycle for KDO engineering tasks.

This component creates isolated working trees and task branches. It is not an
OS/container sandbox, durable workspace registry, or release authorization
mechanism. Callers must enforce authorization and run agents in isolated workers.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import re
import subprocess
from typing import Sequence


class EngineeringWorkspaceError(ValueError):
    """Invalid or unsafe engineering workspace operation."""


@dataclass(frozen=True)
class WorkspaceResult:
    status: str
    path: str
    branch: str
    base_revision: str
    head_revision: str


_SAFE_PART = re.compile(r"[^a-zA-Z0-9._-]+")


def _part(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise EngineeringWorkspaceError(f"{field}_required")
    cleaned = _SAFE_PART.sub("-", value.strip()).strip(".-_")[:40]
    if not cleaned or cleaned in {".", ".."}:
        raise EngineeringWorkspaceError(f"{field}_invalid")
    return cleaned.lower()


def _git(repo: Path, args: Sequence[str], *, timeout_seconds: int = 20) -> str:
    if not 1 <= timeout_seconds <= 60:
        raise EngineeringWorkspaceError("timeout_out_of_range")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(repo),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "LC_ALL": "C",
    }
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(repo), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            errors="replace", timeout=timeout_seconds, shell=False, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise EngineeringWorkspaceError("git_operation_timed_out") from exc
    except OSError as exc:
        raise EngineeringWorkspaceError("git_unavailable") from exc
    if proc.returncode:
        # Keep the diagnostic bounded and avoid returning environment values.
        detail = (proc.stderr or "git_operation_failed").strip().replace("\n", " ")[:300]
        raise EngineeringWorkspaceError(f"git_operation_failed:{detail}")
    return proc.stdout.strip()


def _repository_root(path: str | os.PathLike[str]) -> Path:
    candidate = Path(path).expanduser().resolve()
    if not candidate.exists() or not candidate.is_dir():
        raise EngineeringWorkspaceError("repository_path_must_be_existing_directory")
    root_text = _git(candidate, ["rev-parse", "--show-toplevel"])
    root = Path(root_text).resolve()
    if not (root / ".git").exists() and not (root / ".git").is_file():
        raise EngineeringWorkspaceError("repository_root_not_git_worktree")
    return root


def create_task_worktree(
    repository_path: str | os.PathLike[str],
    managed_root: str | os.PathLike[str],
    *, tenant_id: str, workspace_id: str, task_id: str, base_ref: str = "HEAD",
    timeout_seconds: int = 20,
) -> WorkspaceResult:
    """Create a dedicated task branch and worktree beneath ``managed_root``.

    ``base_ref`` must resolve to a commit in the source repository. The managed
    root must be outside the source repository to prevent generated worktrees
    from being included in source changes or accidentally nested in the repo.
    """
    source = _repository_root(repository_path)
    if not isinstance(base_ref, str) or not base_ref.strip() or "\x00" in base_ref or base_ref.startswith("-"):
        raise EngineeringWorkspaceError("base_ref_invalid")
    if not 1 <= timeout_seconds <= 60:
        raise EngineeringWorkspaceError("timeout_out_of_range")
    base_sha = _git(source, ["rev-parse", "--verify", f"{base_ref.strip()}^{{commit}}"], timeout_seconds=timeout_seconds)
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", base_sha):
        raise EngineeringWorkspaceError("base_ref_did_not_resolve_to_commit")

    root = Path(managed_root).expanduser().resolve()
    if root == source or source in root.parents or root in source.parents:
        raise EngineeringWorkspaceError("managed_root_must_be_outside_source_repository")
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise EngineeringWorkspaceError("managed_root_not_directory")

    tenant = _part(tenant_id, "tenant_id")
    workspace = _part(workspace_id, "workspace_id")
    task = _part(task_id, "task_id")
    identity = hashlib.sha256(f"{tenant_id}\0{workspace_id}\0{task_id}".encode()).hexdigest()[:10]
    branch = f"kdo/{tenant}/{workspace}/{task}-{identity}"
    destination = (root / tenant / workspace / f"{task}-{identity}").resolve()
    if root not in destination.parents:
        raise EngineeringWorkspaceError("workspace_path_escape_detected")
    if destination.exists():
        raise EngineeringWorkspaceError("task_workspace_already_exists")
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        _git(source, ["worktree", "add", "-b", branch, str(destination), base_sha], timeout_seconds=timeout_seconds)
        head = _git(destination, ["rev-parse", "--verify", "HEAD"], timeout_seconds=timeout_seconds)
        if head != base_sha:
            # Do not leave a partially initialized worktree behind.
            try:
                _git(source, ["worktree", "remove", str(destination)], timeout_seconds=timeout_seconds)
            except EngineeringWorkspaceError:
                pass
            raise EngineeringWorkspaceError("workspace_base_revision_mismatch")
    except Exception:
        # Only remove an empty destination created by this invocation. Never
        # recursively delete an existing path or user data.
        if destination.exists() and not (destination / ".git").exists():
            try:
                destination.rmdir()
            except OSError:
                pass
        raise
    return WorkspaceResult("created", str(destination), branch, base_sha, head)


def inspect_task_worktree(workspace_path: str | os.PathLike[str], *, managed_root: str | os.PathLike[str], timeout_seconds: int = 15) -> dict[str, str]:
    """Inspect a managed task worktree without mutating it."""
    root = Path(managed_root).expanduser().resolve()
    path = Path(workspace_path).expanduser().resolve()
    if root == path or root not in path.parents:
        raise EngineeringWorkspaceError("workspace_outside_managed_root")
    if not path.is_dir() or not (path / ".git").exists():
        raise EngineeringWorkspaceError("workspace_not_git_worktree")
    return {
        "status": _git(path, ["status", "--short", "--branch"], timeout_seconds=timeout_seconds),
        "branch": _git(path, ["branch", "--show-current"], timeout_seconds=timeout_seconds),
        "revision": _git(path, ["rev-parse", "--verify", "HEAD"], timeout_seconds=timeout_seconds),
        "diff": _git(path, ["diff", "--no-ext-diff", "--no-textconv", "--", "."], timeout_seconds=timeout_seconds),
    }


def remove_task_worktree(workspace_path: str | os.PathLike[str], *, repository_path: str | os.PathLike[str], managed_root: str | os.PathLike[str], timeout_seconds: int = 20) -> None:
    """Remove a clean managed worktree; dirty worktrees are preserved by default."""
    source = _repository_root(repository_path)
    root = Path(managed_root).expanduser().resolve()
    path = Path(workspace_path).expanduser().resolve()
    if root == path or root not in path.parents:
        raise EngineeringWorkspaceError("workspace_outside_managed_root")
    if not path.is_dir() or not (path / ".git").exists():
        raise EngineeringWorkspaceError("workspace_not_git_worktree")
    status = _git(path, ["status", "--porcelain"], timeout_seconds=timeout_seconds)
    if status:
        raise EngineeringWorkspaceError("dirty_workspace_preserved")
    _git(source, ["worktree", "remove", str(path)], timeout_seconds=timeout_seconds)
