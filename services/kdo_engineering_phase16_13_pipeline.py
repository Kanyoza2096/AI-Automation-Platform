"""Phase 16.13: explicit composition of bounded QA with the isolated Docker runner.

This module is a composition seam, not a worker daemon. The existing approved-plan
workflow/queue remains responsible for admission, lease fencing and workspace creation.
Generated code is never run in the application process by this adapter. Patches are
still applied only through the existing checked Git patch primitive in the task worktree.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
import threading

from services.kdo_engineering_phase16_7 import make_dynamic_quality_executor, DynamicQualityLoopError
from services.kdo_engineering_phase16_12_container_worker import DockerWorkspaceRunner, make_docker_workspace_test_runner

class EngineeringPipelineCompositionError(RuntimeError):
    pass


def make_containerized_dynamic_quality_executor(
    *, workspace_root: str | Path,
    pinned_worker_image: str,
    build_executor: Callable[..., Mapping[str, Any]],
    registry_factory: Callable[[Any, str, tuple[str, ...]], Any],
    repair_capabilities_factory: Callable[[Any, tuple[str, ...]], Iterable[str]],
    authorize: Callable[[str, Mapping[str, Any]], bool],
    test_profile: str = "pytest",
    max_repair_attempts: int = 2,
    test_timeout_seconds: int = 120,
    docker_binary: str = "docker",
    memory_limit: str = "1g",
    cpu_limit: str = "1.0",
    pids_limit: int = 128,
) -> Callable[..., Mapping[str, Any]]:
    """Compose build + Docker-only test runs + dynamic diagnosis/repair/verification.

    The test profile is deployment configuration, not task/model input. The image must
    be pinned by digest. Workspace path is validated by DockerWorkspaceRunner for every
    test invocation. All dynamic loop actions still pass through the injected policy.
    """
    if test_profile not in DockerWorkspaceRunner.ALLOWED_TESTS:
        raise EngineeringPipelineCompositionError("test_profile_not_allowlisted")
    runner = DockerWorkspaceRunner(workspace_root=workspace_root, image=pinned_worker_image,
        docker_binary=docker_binary, timeout_seconds=test_timeout_seconds,
        memory_limit=memory_limit, cpu_limit=cpu_limit, pids_limit=pids_limit)

    def sandboxed_runner_factory(workspace_path: str, task_contract: Any, steps: tuple[str, ...], cancellation_event: threading.Event | None = None):
        # Validate scope at composition and again inside run_tests for each iteration.
        root = Path(workspace_root).resolve()
        workspace = Path(workspace_path).resolve()
        try:
            workspace.relative_to(root)
        except ValueError as exc:
            raise EngineeringPipelineCompositionError("workspace_outside_managed_root") from exc
        if workspace == root or not workspace.is_dir():
            raise EngineeringPipelineCompositionError("task_scoped_workspace_required")
        return make_docker_workspace_test_runner(runner, str(workspace), test_profile=test_profile, cancellation_event=cancellation_event)

    try:
        return make_dynamic_quality_executor(
            build_executor=build_executor,
            registry_factory=registry_factory,
            # This callback is intentionally not invoked in sandboxed mode.
            test_command_factory=lambda *_: (),
            repair_capabilities_factory=repair_capabilities_factory,
            authorize=authorize,
            max_repair_attempts=max_repair_attempts,
            test_timeout_seconds=test_timeout_seconds,
            test_runner_factory=sandboxed_runner_factory,
        )
    except DynamicQualityLoopError as exc:
        raise EngineeringPipelineCompositionError("dynamic_quality_executor_composition_failed") from exc
