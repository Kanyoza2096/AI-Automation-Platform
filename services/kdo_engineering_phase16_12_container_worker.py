"""Phase 16.12: bounded Docker execution for isolated engineering workspaces.

This adapter runs only explicit allowlisted test commands in a disposable container.
It is not enabled by default and does not grant build, patch, approval, or release
authority. The authoritative lifecycle must provide an approved task and workspace.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os, re, shutil, subprocess, time
from typing import Callable, Sequence, Mapping, Any

class ContainerWorkerError(RuntimeError):
    pass

@dataclass(frozen=True)
class ContainerTestResult:
    status: str
    exit_code: int | None
    duration_seconds: float
    stdout: str
    stderr: str
    timed_out: bool
    command: tuple[str, ...]
    sandbox: str = "docker_network_none_readonly_rootfs"

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "exit_code": self.exit_code,
                "duration_seconds": self.duration_seconds, "stdout": self.stdout,
                "stderr": self.stderr, "timed_out": self.timed_out,
                "command": list(self.command), "sandbox": self.sandbox,
                "authoritative": True}

class DockerWorkspaceRunner:
    """Execute allowlisted tests with no network and no host secrets mounted.

    The only host bind mount is the task workspace. The image must be pinned and
    prebuilt by the operator; generated task content cannot select its image.
    """
    ALLOWED_TESTS = {"pytest": ("python", "-m", "pytest"),
                     "unittest": ("python", "-m", "unittest", "discover"),
                     "compileall": ("python", "-m", "compileall", "-q", ".")}

    def __init__(self, *, workspace_root: str | Path, image: str, docker_binary: str = "docker",
                 timeout_seconds: int = 120, max_output_chars: int = 20000,
                 memory_limit: str = "1g", cpu_limit: str = "1.0", pids_limit: int = 128,
                 process_runner: Callable[..., Any] = subprocess.run):
        self.workspace_root = Path(workspace_root).resolve()
        if not isinstance(image, str) or not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", image.strip()):
            raise ContainerWorkerError("pinned_worker_image_digest_required")
        if not isinstance(memory_limit, str) or not re.fullmatch(r"[1-9][0-9]*(?:k|m|g|t)?", memory_limit.lower()):
            raise ContainerWorkerError("memory_limit_invalid")
        if not isinstance(cpu_limit, str) or not re.fullmatch(r"[1-9][0-9]*(?:\.[0-9]+)?", cpu_limit):
            raise ContainerWorkerError("cpu_limit_invalid")
        if not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 300:
            raise ContainerWorkerError("timeout_out_of_range")
        if not isinstance(max_output_chars, int) or not 100 <= max_output_chars <= 100000:
            raise ContainerWorkerError("output_limit_out_of_range")
        if not isinstance(pids_limit, int) or not 16 <= pids_limit <= 512:
            raise ContainerWorkerError("pids_limit_out_of_range")
        self.image, self.docker_binary = image.strip(), docker_binary
        self.timeout_seconds, self.max_output_chars = timeout_seconds, max_output_chars
        self.memory_limit, self.cpu_limit, self.pids_limit = memory_limit, cpu_limit, pids_limit
        self.process_runner = process_runner

    def _workspace(self, workspace: str | Path) -> Path:
        candidate = Path(workspace).resolve()
        try: candidate.relative_to(self.workspace_root)
        except ValueError as exc: raise ContainerWorkerError("workspace_outside_managed_root") from exc
        if not candidate.is_dir(): raise ContainerWorkerError("workspace_not_directory")
        if candidate == self.workspace_root: raise ContainerWorkerError("workspace_must_be_task_scoped_child")
        return candidate

    def run_tests(self, workspace: str | Path, test_profile: str = "pytest") -> dict[str, Any]:
        if test_profile not in self.ALLOWED_TESTS:
            raise ContainerWorkerError("test_profile_not_allowlisted")
        cwd = self._workspace(workspace)
        if any(ch in str(cwd) for ch in (",", "\\")):
            raise ContainerWorkerError("workspace_path_not_mount_safe")
        if self.process_runner is subprocess.run and not Path(self.docker_binary).is_absolute() and shutil.which(self.docker_binary) is None:
            raise ContainerWorkerError("docker_runtime_unavailable")
        cmd = [self.docker_binary, "run", "--rm", "--network", "none", "--read-only",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
               "--pids-limit", str(self.pids_limit), "--memory", self.memory_limit,
               "--cpus", self.cpu_limit, "--user", "65532:65532",
               "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
               "--mount", f"type=bind,src={cwd},dst=/workspace,rw",
               "--workdir", "/workspace", "--env", "CI=1", "--env", "HOME=/tmp",
               "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTEST_ADDOPTS=-p no:cacheprovider",
               self.image, *self.ALLOWED_TESTS[test_profile]]
        start = time.monotonic()
        try:
            proc = self.process_runner(cmd, cwd=str(self.workspace_root), env={"PATH": os.environ.get("PATH", ""), "CI": "1"},
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, errors="replace", timeout=self.timeout_seconds, shell=False, close_fds=True)
            out = str(getattr(proc, "stdout", "") or "")[-self.max_output_chars:]
            err = str(getattr(proc, "stderr", "") or "")[-self.max_output_chars:]
            code = getattr(proc, "returncode", None)
            status = "passed" if code == 0 else "failed"
            return ContainerTestResult(status, code, time.monotonic()-start, out, err, False, tuple(cmd)).to_dict()
        except subprocess.TimeoutExpired as exc:
            def decode(v): return v.decode(errors="replace") if isinstance(v, bytes) else (v or "")
            return ContainerTestResult("timed_out", None, time.monotonic()-start,
                decode(exc.stdout)[-self.max_output_chars:], decode(exc.stderr)[-self.max_output_chars:], True, tuple(cmd)).to_dict()
        except ContainerWorkerError: raise
        except Exception as exc:
            raise ContainerWorkerError("container_test_execution_failed") from exc

def make_docker_workspace_test_runner(runner: DockerWorkspaceRunner, workspace_path: str, *, test_profile: str = "pytest"):
    """Adapt DockerWorkspaceRunner to the bounded quality-loop test callback."""
    if not isinstance(runner, DockerWorkspaceRunner): raise ContainerWorkerError("docker_workspace_runner_required")
    if test_profile not in runner.ALLOWED_TESTS: raise ContainerWorkerError("test_profile_not_allowlisted")
    def run(iteration: int) -> Mapping[str, Any]:
        result = runner.run_tests(workspace_path, test_profile=test_profile)
        return {**result, "iteration": iteration}
    return run
