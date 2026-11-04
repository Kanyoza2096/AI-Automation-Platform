"""Phase 16.12: bounded Docker execution for isolated engineering workspaces.

This adapter runs only explicit allowlisted test commands in a disposable container.
It is not enabled by default and does not grant build, patch, approval, or release
authority. The authoritative lifecycle must provide an approved task and workspace.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections import deque
from pathlib import Path
import os, re, shutil, subprocess, time, uuid, threading
from typing import Callable, Sequence, Mapping, Any


_DOCKER_CLI_ENV_KEYS = frozenset({
    "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH",
    "DOCKER_CONFIG", "DOCKER_API_VERSION", "XDG_RUNTIME_DIR",
})


def docker_cli_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build a minimal Docker CLI environment without forwarding application secrets.

    Only explicit Docker endpoint/TLS/config selectors are inherited. These values
    configure the host-side CLI; they are never passed into the build container.
    """
    source = os.environ if environ is None else environ
    result = {"PATH": str(source.get("PATH", "")), "CI": "1"}
    for key in _DOCKER_CLI_ENV_KEYS:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value
    return result


def _bounded_subprocess_run(cmd: Sequence[str], *, output_limit: int, **kwargs: Any) -> Any:
    """Run the Docker CLI while retaining only a bounded tail of each stream."""
    timeout = kwargs.pop("timeout")
    kwargs.pop("text", None)
    kwargs.pop("errors", None)
    kwargs.pop("stdout", None)
    kwargs.pop("stderr", None)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, **kwargs)
    buffers = {"stdout": deque(), "stderr": deque()}
    sizes = {"stdout": 0, "stderr": 0}
    lock = threading.Lock()
    byte_limit = max(4096, output_limit * 4)

    def drain(name: str, stream: Any) -> None:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            with lock:
                buffers[name].append(chunk)
                sizes[name] += len(chunk)
                while sizes[name] > byte_limit and buffers[name]:
                    excess = sizes[name] - byte_limit
                    first = buffers[name][0]
                    if len(first) <= excess:
                        sizes[name] -= len(buffers[name].popleft())
                    else:
                        buffers[name][0] = first[excess:]
                        sizes[name] -= excess

    readers = [
        threading.Thread(target=drain, args=("stdout", proc.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", proc.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        for reader in readers:
            reader.join()
        def tail(name: str) -> str:
            with lock:
                return b"".join(buffers[name]).decode(errors="replace")[-output_limit:]
        raise subprocess.TimeoutExpired(cmd, timeout, output=tail("stdout"), stderr=tail("stderr"))
    for reader in readers:
        reader.join()
    def tail(name: str) -> str:
        with lock:
            return b"".join(buffers[name]).decode(errors="replace")[-output_limit:]
    return type("BoundedProcessResult", (), {
        "returncode": proc.returncode,
        "stdout": tail("stdout"),
        "stderr": tail("stderr"),
    })()

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
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 300:
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

    def _remove_timed_out_container(self, container_name: str) -> None:
        """Stop and remove the daemon-side container after the client deadline.

        subprocess.run(timeout=...) only terminates the local Docker CLI process;
        it does not guarantee that the Docker daemon stopped the container.
        Explicit removal is therefore part of the hard-timeout contract.
        """
        try:
            cleanup = self.process_runner(
                [self.docker_binary, "rm", "--force", container_name],
                cwd=str(self.workspace_root),
                env=docker_cli_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=10,
                shell=False,
                close_fds=True,
            )
        except Exception as exc:
            raise ContainerWorkerError("container_timeout_cleanup_failed") from exc
        if getattr(cleanup, "returncode", 1) != 0:
            stderr = str(getattr(cleanup, "stderr", "") or "").lower()
            if "no such container" not in stderr and "not found" not in stderr:
                raise ContainerWorkerError("container_timeout_cleanup_failed")

    def run_tests(self, workspace: str | Path, test_profile: str = "pytest", *, cancellation_event: threading.Event | None = None) -> dict[str, Any]:
        if test_profile not in self.ALLOWED_TESTS:
            raise ContainerWorkerError("test_profile_not_allowlisted")
        cwd = self._workspace(workspace)
        if any(ch in str(cwd) for ch in (",", "\\")):
            raise ContainerWorkerError("workspace_path_not_mount_safe")
        if self.process_runner is subprocess.run and not Path(self.docker_binary).is_absolute() and shutil.which(self.docker_binary) is None:
            raise ContainerWorkerError("docker_runtime_unavailable")
        container_name = "kdo-engineering-" + uuid.uuid4().hex
        cmd = [self.docker_binary, "run", "--rm", "--name", container_name, "--network", "none", "--read-only",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--init",
               "--ipc", "private", "--log-driver", "local",
               "--log-opt", "max-size=10m", "--log-opt", "max-file=1",
               "--pids-limit", str(self.pids_limit), "--memory", self.memory_limit,
               "--memory-swap", self.memory_limit, "--cpus", self.cpu_limit,
               "--ulimit", "nofile=1024:1024", "--shm-size", "64m",
               "--user", "65532:65532",
               "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=128m",
               "--mount", f"type=bind,src={cwd},dst=/workspace,rw",
               "--workdir", "/workspace", "--env", "CI=1", "--env", "HOME=/tmp",
               "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTEST_ADDOPTS=-p no:cacheprovider",
               self.image, *self.ALLOWED_TESTS[test_profile]]
        if cancellation_event is not None and not isinstance(cancellation_event, threading.Event):
            raise ContainerWorkerError("cancellation_event_invalid")
        if cancellation_event is not None and cancellation_event.is_set():
            return ContainerTestResult("cancelled", None, 0.0, "", "cancelled_before_start", False, tuple(cmd)).to_dict()
        start = time.monotonic()
        cleanup_lock = threading.Lock()
        cleanup_done = threading.Event()
        cleanup_error: list[BaseException] = []

        def cleanup_once() -> None:
            with cleanup_lock:
                if cleanup_done.is_set():
                    if cleanup_error:
                        raise ContainerWorkerError("container_timeout_cleanup_failed") from cleanup_error[0]
                    return
                try:
                    self._remove_timed_out_container(container_name)
                except BaseException as exc:
                    cleanup_error.append(exc)
                    cleanup_done.set()
                    raise
                cleanup_done.set()

        stop_watchdog = threading.Event()
        watchdog = None
        if cancellation_event is not None:
            def watch_cancellation() -> None:
                while not stop_watchdog.wait(0.05):
                    if cancellation_event.is_set():
                        try:
                            cleanup_once()
                        except BaseException:
                            return
                        return
            watchdog = threading.Thread(target=watch_cancellation, name="kdo-container-cancellation", daemon=True)
            watchdog.start()
        try:
            run_kwargs = dict(cwd=str(self.workspace_root), env={"PATH": os.environ.get("PATH", ""), "CI": "1"},
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, errors="replace", timeout=self.timeout_seconds, shell=False, close_fds=True)
            if self.process_runner is subprocess.run:
                proc = _bounded_subprocess_run(cmd, output_limit=self.max_output_chars, **run_kwargs)
            else:
                proc = self.process_runner(cmd, **run_kwargs)
            if watchdog is not None:
                stop_watchdog.set()
                watchdog.join(timeout=11)
                if watchdog.is_alive():
                    raise ContainerWorkerError("container_cancellation_cleanup_timeout")
            if cleanup_error:
                raise ContainerWorkerError("container_timeout_cleanup_failed") from cleanup_error[0]
            out = str(getattr(proc, "stdout", "") or "")[-self.max_output_chars:]
            err = str(getattr(proc, "stderr", "") or "")[-self.max_output_chars:]
            code = getattr(proc, "returncode", None)
            if cancellation_event is not None and cancellation_event.is_set():
                if not cleanup_done.is_set():
                    cleanup_once()
                result = ContainerTestResult("cancelled", code, time.monotonic()-start, out, err, False, tuple(cmd)).to_dict()
                result["container_cleanup"] = "confirmed"
                return result
            status = "passed" if code == 0 else "failed"
            return ContainerTestResult(status, code, time.monotonic()-start, out, err, False, tuple(cmd)).to_dict()
        except subprocess.TimeoutExpired as exc:
            stop_watchdog.set()
            if watchdog is not None:
                watchdog.join(timeout=11)
            if not cleanup_done.is_set():
                cleanup_once()
            if cleanup_error:
                raise ContainerWorkerError("container_timeout_cleanup_failed") from cleanup_error[0]
            def decode(v): return v.decode(errors="replace") if isinstance(v, bytes) else (v or "")
            if cancellation_event is not None and cancellation_event.is_set():
                result = ContainerTestResult("cancelled", None, time.monotonic()-start,
                    decode(exc.stdout)[-self.max_output_chars:], decode(exc.stderr)[-self.max_output_chars:], False, tuple(cmd)).to_dict()
            else:
                result = ContainerTestResult("timed_out", None, time.monotonic()-start,
                    decode(exc.stdout)[-self.max_output_chars:], decode(exc.stderr)[-self.max_output_chars:], True, tuple(cmd)).to_dict()
            result["container_cleanup"] = "confirmed"
            return result
        except ContainerWorkerError: raise
        except Exception as exc:
            raise ContainerWorkerError("container_test_execution_failed") from exc
        finally:
            stop_watchdog.set()
            if watchdog is not None:
                watchdog.join(timeout=11)

def make_docker_workspace_test_runner(runner: DockerWorkspaceRunner, workspace_path: str, *, test_profile: str = "pytest", cancellation_event: threading.Event | None = None):
    """Adapt DockerWorkspaceRunner to the bounded quality-loop test callback."""
    if not isinstance(runner, DockerWorkspaceRunner): raise ContainerWorkerError("docker_workspace_runner_required")
    if test_profile not in runner.ALLOWED_TESTS: raise ContainerWorkerError("test_profile_not_allowlisted")
    def run(iteration: int) -> Mapping[str, Any]:
        result = runner.run_tests(workspace_path, test_profile=test_profile, cancellation_event=cancellation_event)
        return {**result, "iteration": iteration}
    return run
