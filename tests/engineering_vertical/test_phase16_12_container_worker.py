from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import os
import pytest
from services.kdo_engineering_phase16_12_container_worker import (
    ContainerWorkerError, DockerWorkspaceRunner, make_docker_workspace_test_runner,
)

def test_container_command_has_isolation_and_resource_controls(tmp_path):
    root = tmp_path / "workspaces"; root.mkdir(); task = root / "task-1"; task.mkdir()
    seen = {}
    def fake(cmd, **kwargs):
        seen["cmd"] = cmd; seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")
    runner = DockerWorkspaceRunner(workspace_root=root, image="python:3.12-slim@sha256:" + "a" * 64, process_runner=fake)
    result = runner.run_tests(task)
    cmd = seen["cmd"]
    assert result["status"] == "passed" and result["authoritative"] is True
    assert "--network" in cmd and cmd[cmd.index("--network") + 1] == "none"
    assert "--read-only" in cmd and "--cap-drop" in cmd and "ALL" in cmd
    assert "--pids-limit" in cmd and "--memory" in cmd and "--cpus" in cmd
    assert cmd[cmd.index("--memory-swap") + 1] == cmd[cmd.index("--memory") + 1]
    assert cmd[cmd.index("--log-driver") + 1] == "local"
    assert cmd[cmd.index("--log-opt") + 1] == "max-size=10m"
    assert cmd[cmd.index("--ulimit") + 1] == "nofile=1024:1024"
    assert "--init" in cmd and "--ipc" in cmd and "--shm-size" in cmd
    assert "nodev" in cmd[cmd.index("--tmpfs") + 1]
    assert seen["kwargs"]["shell"] is False
    assert seen["kwargs"]["env"].keys() == {"PATH", "CI"}
    assert "--mount" in cmd and str(task.resolve()) in cmd[cmd.index("--mount") + 1]

def test_workspace_escape_and_root_mount_are_rejected(tmp_path):
    root = tmp_path / "workspaces"; root.mkdir(); task = root / "task"; task.mkdir()
    runner = DockerWorkspaceRunner(workspace_root=root, image="python:3.12-slim@sha256:" + "a" * 64, process_runner=lambda *a, **k: None)
    with pytest.raises(ContainerWorkerError, match="workspace_outside_managed_root"):
        runner.run_tests(tmp_path)
    with pytest.raises(ContainerWorkerError, match="workspace_must_be_task_scoped_child"):
        runner.run_tests(root)

def test_unknown_test_profile_is_rejected(tmp_path):
    root = tmp_path / "workspaces"; root.mkdir(); task = root / "task"; task.mkdir()
    runner = DockerWorkspaceRunner(workspace_root=root, image="python:3.12-slim@sha256:" + "a" * 64, process_runner=lambda *a, **k: None)
    with pytest.raises(ContainerWorkerError, match="test_profile_not_allowlisted"):
        runner.run_tests(task, "bash")

def test_quality_loop_adapter_preserves_iteration_and_evidence(tmp_path):
    root = tmp_path / "workspaces"; root.mkdir(); task = root / "task"; task.mkdir()
    runner = DockerWorkspaceRunner(workspace_root=root, image="python:3.12-slim@sha256:" + "a" * 64, process_runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout="tests passed", stderr=""))
    callback = make_docker_workspace_test_runner(runner, str(task))
    result = callback(2)
    assert result["iteration"] == 2 and result["exit_code"] == 0 and result["sandbox"].startswith("docker_")


def test_timeout_removes_daemon_container_after_docker_cli_deadline(tmp_path):
    root = tmp_path / "workspaces"; root.mkdir()
    task = root / "task-timeout"; task.mkdir()
    calls = []

    def fake(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd, timeout=1, output="partial", stderr="deadline")
        return SimpleNamespace(returncode=0, stdout="removed", stderr="")

    runner = DockerWorkspaceRunner(
        workspace_root=root,
        image="python:3.12-slim@sha256:" + "a" * 64,
        timeout_seconds=1,
        process_runner=fake,
    )
    result = runner.run_tests(task)
    assert result["status"] == "timed_out"
    assert result["container_cleanup"] == "confirmed"
    assert "--name" in calls[0]
    container_name = calls[0][calls[0].index("--name") + 1]
    assert calls[1] == ["docker", "rm", "--force", container_name]


def test_timeout_fails_closed_when_daemon_container_cleanup_fails(tmp_path):
    root = tmp_path / "workspaces"; root.mkdir()
    task = root / "task-timeout"; task.mkdir()

    def fake(cmd, **kwargs):
        if cmd[1] == "run":
            raise subprocess.TimeoutExpired(cmd, timeout=1)
        return SimpleNamespace(returncode=1, stdout="", stderr="daemon unavailable")

    runner = DockerWorkspaceRunner(
        workspace_root=root,
        image="python:3.12-slim@sha256:" + "a" * 64,
        timeout_seconds=1,
        process_runner=fake,
    )
    with pytest.raises(ContainerWorkerError, match="container_timeout_cleanup_failed"):
        runner.run_tests(task)


def test_lease_or_shutdown_cancellation_removes_running_container(tmp_path):
    import threading

    root = tmp_path / "workspaces"; root.mkdir()
    task = root / "task-cancel"; task.mkdir()
    cancellation = threading.Event()
    cleanup_started = threading.Event()
    calls = []

    def fake(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "run":
            assert cleanup_started.wait(2), "container cleanup was not requested"
            return SimpleNamespace(returncode=137, stdout="", stderr="container stopped")
        cleanup_started.set()
        return SimpleNamespace(returncode=0, stdout="removed", stderr="")

    runner = DockerWorkspaceRunner(
        workspace_root=root,
        image="python:3.12-slim@sha256:" + "a" * 64,
        timeout_seconds=5,
        process_runner=fake,
    )
    timer = threading.Timer(0.05, cancellation.set)
    timer.start()
    try:
        result = runner.run_tests(task, cancellation_event=cancellation)
    finally:
        timer.cancel()
    assert result["status"] == "cancelled"
    assert result["container_cleanup"] == "confirmed"
    assert calls[1][1:3] == ["rm", "--force"]


def test_cancellation_before_start_does_not_launch_container(tmp_path):
    import threading

    root = tmp_path / "workspaces"; root.mkdir()
    task = root / "task-cancel"; task.mkdir()
    cancellation = threading.Event()
    cancellation.set()
    calls = []
    runner = DockerWorkspaceRunner(
        workspace_root=root,
        image="python:3.12-slim@sha256:" + "a" * 64,
        process_runner=lambda *a, **k: calls.append(a) or SimpleNamespace(returncode=0),
    )
    result = runner.run_tests(task, cancellation_event=cancellation)
    assert result["status"] == "cancelled"
    assert not calls


def test_default_docker_cli_capture_is_bounded_in_memory(tmp_path):
    from services.kdo_engineering_phase16_12_container_worker import _bounded_subprocess_run

    result = _bounded_subprocess_run(
        [sys.executable, "-c", "import sys; print('x' * 200000); print('y' * 200000, file=sys.stderr)"],
        output_limit=128,
        cwd=str(tmp_path),
        env={"PATH": os.environ.get("PATH", "")},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        timeout=5,
        shell=False,
        close_fds=True,
    )
    assert len(result.stdout) <= 128
    assert len(result.stderr) <= 128
    assert result.stdout.endswith("x" * 128)
    assert result.stderr.endswith("y" * 128)
