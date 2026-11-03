from pathlib import Path
from types import SimpleNamespace
import pytest

from services.kdo_engineering_agent_registry import AgentRegistry
from services.kdo_engineering_phase16_13_pipeline import (
    EngineeringPipelineCompositionError, make_containerized_dynamic_quality_executor,
)


def _registry():
    registry = AgentRegistry()
    registry.register_adapter("debugging_engineer", lambda a: {"agent_id":"debug-1", "status":"succeeded", "summary":"diagnosis", "evidence":{}})
    registry.register_adapter("backend_engineer", lambda a: {"agent_id":"repair-1", "status":"succeeded", "summary":"repair", "evidence":{"patch_text":"diff --git a/a b/a\n"}})
    registry.register_adapter("independent_reviewer", lambda a: {"agent_id":"verify-1", "status":"succeeded", "summary":"verified", "evidence":{"verification_status":"approved", "verification_id":"v-1"}})
    return registry


def test_pipeline_composition_uses_sandbox_runner_and_never_host_command_factory(tmp_path, monkeypatch):
    root = tmp_path / "managed"; root.mkdir()
    task_dir = root / "task-1"; task_dir.mkdir()
    calls = []
    class FakeDockerRunner:
        ALLOWED_TESTS = {"pytest", "unittest", "compileall"}
        def __init__(self, **kwargs): calls.append(("runner_init", kwargs))
        def run_tests(self, workspace, test_profile="pytest", cancellation_event=None):
            calls.append(("sandbox_test", str(workspace), test_profile, cancellation_event))
            return {"status":"passed", "exit_code":0, "stdout":"ok", "stderr":"", "authoritative":True}
    monkeypatch.setattr("services.kdo_engineering_phase16_13_pipeline.DockerWorkspaceRunner", FakeDockerRunner)
    monkeypatch.setattr("services.kdo_engineering_phase16_13_pipeline.make_docker_workspace_test_runner",
        lambda runner, path, test_profile="pytest", cancellation_event=None: lambda i: runner.run_tests(path, test_profile, cancellation_event))
    task = SimpleNamespace(task_id="task-1", tenant_id="tenant-1", workspace_id="workspace-1",
        objective="validate change", acceptance_criteria=("tests pass",))
    executor = make_containerized_dynamic_quality_executor(
        workspace_root=root, pinned_worker_image="python:3.12@sha256:" + "a"*64,
        build_executor=lambda path, contract, steps: {"build_status":"completed"},
        registry_factory=lambda *args: _registry(),
        repair_capabilities_factory=lambda *args: {"backend"}, authorize=lambda action, task: True)
    result = executor(str(task_dir), task, ("inspect", "implement", "test"))
    assert result["quality_loop"]["status"] == "awaiting_authorization"
    assert result["quality_loop"]["release_authorized"] is False
    assert any(x[0] == "sandbox_test" for x in calls)


def test_pipeline_rejects_unallowlisted_test_profile(tmp_path):
    root = tmp_path / "managed"; root.mkdir()
    with pytest.raises(EngineeringPipelineCompositionError, match="test_profile_not_allowlisted"):
        make_containerized_dynamic_quality_executor(
            workspace_root=root, pinned_worker_image="python:3.12@sha256:" + "a"*64,
            build_executor=lambda *a: {}, registry_factory=lambda *a: _registry(),
            repair_capabilities_factory=lambda *a: {"backend"}, authorize=lambda *a: True,
            test_profile="bash")


def test_pipeline_rejects_workspace_outside_managed_root(tmp_path, monkeypatch):
    root = tmp_path / "managed"; root.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    class FakeDockerRunner:
        ALLOWED_TESTS = {"pytest", "unittest", "compileall"}
        def __init__(self, **kwargs): pass
    monkeypatch.setattr("services.kdo_engineering_phase16_13_pipeline.DockerWorkspaceRunner", FakeDockerRunner)
    task = SimpleNamespace(task_id="task-1", tenant_id="tenant-1", workspace_id="workspace-1",
        objective="validate change", acceptance_criteria=("tests pass",))
    executor = make_containerized_dynamic_quality_executor(
        workspace_root=root, pinned_worker_image="python:3.12@sha256:" + "a"*64,
        build_executor=lambda *a: {}, registry_factory=lambda *a: _registry(),
        repair_capabilities_factory=lambda *a: {"backend"}, authorize=lambda *a: True)
    with pytest.raises(EngineeringPipelineCompositionError, match="workspace_outside_managed_root"):
        executor(str(outside), task, ("inspect",))


def test_containerized_executor_forwards_cancellation_to_test_runner(tmp_path, monkeypatch):
    import threading
    root = tmp_path / "managed"; root.mkdir()
    task_dir = root / "task-cancel"; task_dir.mkdir()
    captured = {}

    class FakeDockerRunner:
        ALLOWED_TESTS = {"pytest", "unittest", "compileall"}
        def __init__(self, **kwargs): pass
        def run_tests(self, workspace, test_profile="pytest", cancellation_event=None):
            captured["event"] = cancellation_event
            return {"status": "passed", "exit_code": 0, "authoritative": True}

    monkeypatch.setattr("services.kdo_engineering_phase16_13_pipeline.DockerWorkspaceRunner", FakeDockerRunner)
    monkeypatch.setattr(
        "services.kdo_engineering_phase16_13_pipeline.make_docker_workspace_test_runner",
        lambda runner, path, test_profile="pytest", cancellation_event=None:
            lambda i: runner.run_tests(path, test_profile, cancellation_event),
    )
    task = SimpleNamespace(task_id="task-1", tenant_id="tenant-1", workspace_id="workspace-1",
        objective="validate change", acceptance_criteria=("tests pass",))
    executor = make_containerized_dynamic_quality_executor(
        workspace_root=root, pinned_worker_image="python:3.12@sha256:" + "a"*64,
        build_executor=lambda *a: {"build_status": "completed"},
        registry_factory=lambda *a: _registry(),
        repair_capabilities_factory=lambda *a: {"backend"}, authorize=lambda *a: True)
    cancellation = threading.Event()
    executor(str(task_dir), task, ("inspect",), cancellation_event=cancellation)
    assert captured["event"] is cancellation
