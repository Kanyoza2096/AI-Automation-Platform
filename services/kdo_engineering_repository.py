"""Safe, local Git inspection and patch application primitives for KDO's engineering vertical.

This module is deliberately bounded: it never pushes, fetches, commits, or deploys.
It is NOT a sandbox. Callers must run it against a disposable workspace in an isolated
container/VM and enforce policy/approval outside this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import subprocess
from typing import Sequence


class RepositoryInputError(ValueError):
    """Raised when repository inputs are invalid or unsafe."""


@dataclass(frozen=True)
class RepositoryResult:
    status: str
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = False


def _validate_workspace(workspace: str | os.PathLike[str]) -> Path:
    path = Path(workspace).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise RepositoryInputError("workspace must be an existing directory")
    if not (path / ".git").exists():
        raise RepositoryInputError("workspace must be a Git working tree")
    return path


def _run_git(repo: Path, args: Sequence[str], *, timeout_seconds: int = 15,
             max_output_chars: int = 20000) -> RepositoryResult:
    if not isinstance(args, (list, tuple)) or not args:
        raise RepositoryInputError("git arguments must be a non-empty list")
    if any(not isinstance(arg, str) or "\x00" in arg for arg in args):
        raise RepositoryInputError("git arguments must be strings without NUL bytes")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 60:
        raise RepositoryInputError("timeout_seconds must be an integer from 1 to 60")
    if not isinstance(max_output_chars, int) or isinstance(max_output_chars, bool) or not 100 <= max_output_chars <= 100000:
        raise RepositoryInputError("max_output_chars must be an integer from 100 to 100000")

    # Only this fixed Git executable is invoked; no shell and no user-supplied executable.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(repo),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "LC_ALL": "C",
    }
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        return RepositoryResult("timed_out", 124, (stdout + "")[:max_output_chars],
                                (stderr + "\nGit command timed out")[:max_output_chars])
    except OSError as exc:
        return RepositoryResult("failed", 127, "", f"Unable to start Git: {exc}"[:max_output_chars])

    stdout, stderr = proc.stdout, proc.stderr
    truncated = len(stdout) > max_output_chars or len(stderr) > max_output_chars
    stdout = stdout[:max_output_chars]
    stderr = stderr[:max_output_chars]
    return RepositoryResult("passed" if proc.returncode == 0 else "failed",
                            proc.returncode, stdout, stderr, truncated)


def inspect_repository(workspace: str | os.PathLike[str], *,
                       timeout_seconds: int = 15,
                       max_output_chars: int = 20000) -> dict[str, RepositoryResult]:
    """Return bounded status, current revision, and diff evidence; no mutation."""
    repo = _validate_workspace(workspace)
    return {
        "status": _run_git(repo, ["status", "--short", "--branch"], timeout_seconds=timeout_seconds,
                           max_output_chars=max_output_chars),
        "revision": _run_git(repo, ["rev-parse", "--verify", "HEAD"], timeout_seconds=timeout_seconds,
                             max_output_chars=max_output_chars),
        "diff": _run_git(repo, ["diff", "--no-ext-diff", "--no-textconv", "--", "."],
                         timeout_seconds=timeout_seconds, max_output_chars=max_output_chars),
    }


def apply_patch(workspace: str | os.PathLike[str], patch_text: str, *,
                timeout_seconds: int = 15,
                max_output_chars: int = 20000) -> RepositoryResult:
    """Apply a unified diff to a disposable worktree without invoking a shell.

    The patch is passed on stdin to `git apply --check` first. If that succeeds, the
    same patch is applied. This function does not commit or push. External policy must
    authorize this mutation before the function is called.
    """
    repo = _validate_workspace(workspace)
    if not isinstance(patch_text, str) or not patch_text.strip():
        raise RepositoryInputError("patch_text must be a non-empty string")
    if "\x00" in patch_text:
        raise RepositoryInputError("patch_text must not contain NUL bytes")
    if len(patch_text.encode("utf-8")) > 2_000_000:
        raise RepositoryInputError("patch_text exceeds 2 MB limit")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 60:
        raise RepositoryInputError("timeout_seconds must be an integer from 1 to 60")
    if not isinstance(max_output_chars, int) or isinstance(max_output_chars, bool) or not 100 <= max_output_chars <= 100000:
        raise RepositoryInputError("max_output_chars must be an integer from 100 to 100000")

    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(repo),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "LC_ALL": "C",
    }
    # --check avoids partial changes when the patch cannot be applied cleanly.
    for args in (["git", "apply", "--check", "--whitespace=error"], ["git", "apply", "--whitespace=error"]):
        try:
            proc = subprocess.run(
                args, cwd=str(repo), env=env, input=patch_text, stdin=None,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                errors="replace", timeout=timeout_seconds, shell=False, check=False,
            )
        except subprocess.TimeoutExpired:
            return RepositoryResult("timed_out", 124, "", "Git patch operation timed out")
        except OSError as exc:
            return RepositoryResult("failed", 127, "", f"Unable to start Git: {exc}"[:max_output_chars])
        if proc.returncode != 0:
            return RepositoryResult("failed", proc.returncode, proc.stdout[:max_output_chars],
                                    proc.stderr[:max_output_chars],
                                    len(proc.stdout) > max_output_chars or len(proc.stderr) > max_output_chars)
    return RepositoryResult("passed", 0, proc.stdout[:max_output_chars], proc.stderr[:max_output_chars],
                            len(proc.stdout) > max_output_chars or len(proc.stderr) > max_output_chars)



def reverse_patch(workspace: str | os.PathLike[str], patch_text: str, *,
                  timeout_seconds: int = 15) -> RepositoryResult:
    """Reverse only the supplied patch; never reset or clean unrelated work."""
    repo = _validate_workspace(workspace)
    if not isinstance(patch_text, str) or not patch_text.strip():
        raise RepositoryInputError("patch_text must be a non-empty string")
    try:
        proc = subprocess.run(
            ["git", "apply", "--reverse", "--whitespace=nowarn", "-"],
            cwd=str(repo),
            env={"PATH": os.environ.get("PATH", ""), "HOME": str(repo),
                 "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
                 "GIT_CONFIG_GLOBAL": os.devnull, "LC_ALL": "C"},
            input=patch_text, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", timeout=timeout_seconds, shell=False, check=False,
        )
    except subprocess.TimeoutExpired:
        return RepositoryResult("timed_out", 124, "", "Git reverse patch timed out")
    except OSError as exc:
        return RepositoryResult("failed", 127, "", f"Unable to start Git: {exc}")
    return RepositoryResult("passed" if proc.returncode == 0 else "failed",
                            proc.returncode, proc.stdout[:20000], proc.stderr[:20000])
