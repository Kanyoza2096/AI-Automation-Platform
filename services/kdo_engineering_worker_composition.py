"""Explicit PostgreSQL + governed workflow + containerized QA composition.

The host must inject the actual Action Kernel-backed authorization callbacks,
agent registry adapters, and build executor. This module does not invent agent
providers or grant policy. It never starts a worker or runs Docker at import time.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from services.kdo_engineering_phase16_4 import PostgresEngineeringBuildQueue
from services.kdo_engineering_phase16_6 import create_postgres_engineering_workflow
from services.kdo_engineering_phase16_13_pipeline import (
    EngineeringPipelineCompositionError,
    make_containerized_dynamic_quality_executor,
)
from services.kdo_engineering_worker_bootstrap import (
    ProductionWorkerConfig,
    validate_production_worker_config,
)


class EngineeringWorkerCompositionError(RuntimeError):
    """Missing or invalid host-owned dependencies for worker composition."""


def build_worker_dependencies(
    *,
    config: ProductionWorkerConfig,
    connection_factory: Callable[[], Any],
    source_repository_path: str | Path,
    managed_workspace_root: str | Path,
    pinned_worker_image: str,
    workflow_authorize: Callable[[str, str, str, str], bool],
    build_executor: Callable[..., Mapping[str, Any]],
    registry_factory: Callable[[Any, str, tuple[str, ...]], Any],
    repair_capabilities_factory: Callable[[Any, tuple[str, ...]], Iterable[str]],
    pipeline_authorize: Callable[[str, Mapping[str, Any]], bool],
    admission_receipt_verifier: Callable[[Mapping[str, Any], Mapping[str, Any]], Any],
    docker_binary: str = "docker",
    test_profile: str = "pytest",
    test_timeout_seconds: int = 120,
    max_repair_attempts: int = 2,
    memory_limit: str = "1g",
    cpu_limit: str = "1.0",
    pids_limit: int = 128,
) -> dict[str, Any]:
    """Compose the real PostgreSQL queue and task workflow with isolated QA.

    Every authority-bearing callback and the initial build adapter is supplied by
    the trusted host. Generated task text cannot select providers, policy, Docker
    image, command profile, or resource limits.
    """
    validate_production_worker_config(config)
    if not callable(connection_factory):
        raise EngineeringWorkerCompositionError("postgres_connection_factory_required")
    required_callbacks = {
        "workflow_authorize": workflow_authorize,
        "build_executor": build_executor,
        "registry_factory": registry_factory,
        "repair_capabilities_factory": repair_capabilities_factory,
        "pipeline_authorize": pipeline_authorize,
        "admission_receipt_verifier": admission_receipt_verifier,
    }
    missing = [name for name, callback in required_callbacks.items() if not callable(callback)]
    if missing:
        raise EngineeringWorkerCompositionError("host_owned_dependencies_required:" + ",".join(missing))
    if not isinstance(pinned_worker_image, str) or "@sha256:" not in pinned_worker_image:
        raise EngineeringWorkerCompositionError("digest_pinned_worker_image_required")

    try:
        workflow = create_postgres_engineering_workflow(
            connection_factory=connection_factory,
            source_repository_path=source_repository_path,
            managed_root=managed_workspace_root,
            authorize=workflow_authorize,
        )
        queue = PostgresEngineeringBuildQueue(connection_factory)
        executor = make_containerized_dynamic_quality_executor(
            workspace_root=managed_workspace_root,
            pinned_worker_image=pinned_worker_image,
            build_executor=build_executor,
            registry_factory=registry_factory,
            repair_capabilities_factory=repair_capabilities_factory,
            authorize=pipeline_authorize,
            test_profile=test_profile,
            max_repair_attempts=max_repair_attempts,
            test_timeout_seconds=test_timeout_seconds,
            docker_binary=docker_binary,
            memory_limit=memory_limit,
            cpu_limit=cpu_limit,
            pids_limit=pids_limit,
        )
    except (ValueError, EngineeringPipelineCompositionError) as exc:
        raise EngineeringWorkerCompositionError("governed_worker_composition_failed") from exc

    return {
        "queue": queue,
        "workflow": workflow,
        "executor": executor,
        "admission_receipt_verifier": admission_receipt_verifier,
        "cleanup": lambda: None,
    }



def build_gateway_worker_dependencies(
    *,
    config: ProductionWorkerConfig,
    connection_factory: Callable[[], Any],
    source_repository_path: str | Path,
    managed_workspace_root: str | Path,
    pinned_worker_image: str,
    gateway: Any,
    workflow_authorize: Callable[[str, str, str, str], bool],
    pipeline_authorize: Callable[[str, Mapping[str, Any]], bool] | None = None,
    admission_receipt_verifier: Callable[[Mapping[str, Any], Mapping[str, Any]], Any],
    repair_capabilities_factory: Callable[[Any, tuple[str, ...]], Iterable[str]],
    action_kernel: Any = None,
    event_store: Any = None,
    docker_binary: str = "docker",
    test_profile: str = "pytest",
    test_timeout_seconds: int = 120,
    max_repair_attempts: int = 2,
    memory_limit: str = "1g",
    cpu_limit: str = "1.0",
    pids_limit: int = 128,
) -> dict[str, Any]:
    """Compose the existing LLM gateway with governed builder and specialist roles.

    This is the concrete host-side adapter path for a trusted worker factory.
    The Action Kernel callbacks and digest-pinned worker image remain mandatory.
    """
    from services.kdo_engineering_gateway_agents import (
        make_gateway_build_executor,
        make_gateway_quality_registry_factory,
    )

    if gateway is None or not callable(getattr(gateway, "complete_structured", None)):
        raise EngineeringWorkerCompositionError("configured_llm_gateway_required")
    if not callable(repair_capabilities_factory):
        raise EngineeringWorkerCompositionError("task_aware_repair_capability_factory_required")
    worker_authorizer = None
    if pipeline_authorize is None:
        if action_kernel is None:
            raise EngineeringWorkerCompositionError("durable_action_kernel_required_for_worker_authorization")
        try:
            from services.kdo_engineering_worker_authorization import (
                build_council_action_kernel_worker_authorizer,
            )
            worker_authorizer = build_council_action_kernel_worker_authorizer(
                action_kernel, event_store=event_store,
            )
        except Exception as exc:
            raise EngineeringWorkerCompositionError("council_action_kernel_authorizer_unavailable") from exc
        pipeline_authorize = worker_authorizer.authorize
    else:
        candidate = getattr(pipeline_authorize, "__self__", pipeline_authorize)
        if not callable(getattr(candidate, "bind_execution_context", None)):
            raise EngineeringWorkerCompositionError(
                "scoped_council_action_kernel_authorizer_required"
            )
        worker_authorizer = candidate
    if not callable(pipeline_authorize):
        raise EngineeringWorkerCompositionError("pipeline_authorizer_required")
    workspace_id = config.workspace_id
    build_executor = make_gateway_build_executor(
        gateway=gateway, workspace_root=str(managed_workspace_root),
        authorize=pipeline_authorize,
    )
    registry_factory = make_gateway_quality_registry_factory(
        gateway=gateway, workspace_id=workspace_id,
    )
    dependencies = build_worker_dependencies(
        config=config,
        connection_factory=connection_factory,
        source_repository_path=source_repository_path,
        managed_workspace_root=managed_workspace_root,
        pinned_worker_image=pinned_worker_image,
        workflow_authorize=workflow_authorize,
        build_executor=build_executor,
        registry_factory=registry_factory,
        repair_capabilities_factory=repair_capabilities_factory,
        pipeline_authorize=pipeline_authorize,
        admission_receipt_verifier=admission_receipt_verifier,
        docker_binary=docker_binary,
        test_profile=test_profile,
        test_timeout_seconds=test_timeout_seconds,
        max_repair_attempts=max_repair_attempts,
        memory_limit=memory_limit,
        cpu_limit=cpu_limit,
        pids_limit=pids_limit,
    )
    if worker_authorizer is not None:
        # The queue supervisor binds the exact claimed job/plan/lease around this
        # executor invocation. The pipeline's per-action callback reads that
        # thread-local context and fails closed when it is absent.
        dependencies["executor"].worker_authorizer = worker_authorizer
    return dependencies
