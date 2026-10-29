"""Bounded local test runner for KDO engineering tasks.

Security note: this is a development runner, NOT an OS/container security sandbox.
Run only in an already isolated worker/container with network and secret access
restricted by the deployment environment. It never grants execution authority.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os, subprocess, tempfile, time
from typing import Sequence

@dataclass(frozen=True)
class RunnerResult:
    status: str
    exit_code: int | None
    duration_seconds: float
    stdout: str
    stderr: str
    timed_out: bool
    command: tuple[str, ...]

class RunnerInputError(ValueError):
    pass

def run_command(workspace: str | Path, command: Sequence[str], *, timeout_seconds: int = 60,
                max_output_chars: int = 20000, allowed_commands: tuple[str, ...] = ("pytest", "python", "python3")) -> RunnerResult:
    """Run an explicitly allowlisted command in a workspace with bounded time/output.

    Caller must perform authorization before calling. Workspace must be a dedicated
    disposable worker directory. No shell is used; command arguments are passed
    directly. The environment is minimized to avoid leaking parent secrets.
    """
    if not isinstance(command, (list, tuple)) or not command or not all(isinstance(x, str) and x for x in command):
        raise RunnerInputError("command_required")
    if command[0] not in allowed_commands:
        raise RunnerInputError("command_not_allowlisted")
    if not isinstance(timeout_seconds, int) or timeout_seconds < 1 or timeout_seconds > 300:
        raise RunnerInputError("timeout_out_of_range")
    if not isinstance(max_output_chars, int) or max_output_chars < 100 or max_output_chars > 100000:
        raise RunnerInputError("output_limit_out_of_range")
    cwd = Path(workspace).resolve()
    if not cwd.is_dir():
        raise RunnerInputError("workspace_not_directory")
    start = time.monotonic()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(cwd), "CI": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        proc = subprocess.run(list(command), cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
            timeout=timeout_seconds, shell=False, close_fds=True)
        duration = time.monotonic() - start
        stdout = proc.stdout[-max_output_chars:]
        stderr = proc.stderr[-max_output_chars:]
        status = "passed" if proc.returncode == 0 else "failed"
        return RunnerResult(status, proc.returncode, duration, stdout, stderr, False, tuple(command))
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        def decode(value):
            if value is None: return ""
            return value.decode(errors="replace") if isinstance(value, bytes) else value
        return RunnerResult("timed_out", None, duration, decode(exc.stdout)[-max_output_chars:],
                            decode(exc.stderr)[-max_output_chars:], True, tuple(command))
