from pathlib import Path
from types import SimpleNamespace
import subprocess
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
