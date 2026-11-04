"""Trusted host composition for the KDO Engineering Vertical worker.

This module derives the worker-action authorizer and durable receipt verifier
from the configured Action Kernel. The host still supplies the authenticated
workflow-scope authorizer, PostgreSQL connection factory, configured LLM gateway,
and task-aware repair capabilities. It never starts a worker at import time.
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from services.kdo_engineering_receipts import build_durable_action_receipt_verifier
from services.kdo_engineering_worker_authorization import (
    build_council_action_kernel_worker_authorizer,
)
from services.kdo_engineering_worker_bootstrap import (
    ProductionWorkerConfig,
    run_production_engineering_worker,
    validate_production_worker_config,
)
from services.kdo_engineering_worker_composition import (
    EngineeringWorkerCompositionError,
    build_gateway_worker_dependencies,
)


class TrustedEngineeringWorkerHostError(RuntimeError):
    """The trusted worker host cannot be composed without durable governance."""


def build_trusted_gateway_worker_host(
    *,
    config: ProductionWorkerConfig,
    connection_factory: Callable[[], Any],
    source_repository_path: str | Path,
    managed_workspace_root: str | Path,
    pinned_worker_image: str,
    gateway: Any,
    action_kernel: Any,
    authenticated_workflow_authorizer: Callable[[str, str, str, str], bool],
    repair_capabilities_factory: Callable[[Any, tuple[str, ...]], Iterable[str]],
    event_store: Any = None,
    docker_binary: str = "docker",
    test_profile: str = "pytest",
    test_timeout_seconds: int = 120,
    max_repair_attempts: int = 2,
    memory_limit: str = "1g",
    cpu_limit: str = "1.0",
    pids_limit: int = 128,
) -> dict[str, Any]:
    """Build a worker host using the canonical durable Action Kernel database.

    The authenticated workflow authorizer is deliberately distinct from worker
    action authorization: it enforces the actor's tenant/workspace permissions
    when the persisted workflow is accessed. Each mutating worker operation is
    separately authorized through Council + Action Kernel with a bound job,
    approved plan, and active lease.

    No database connection, Docker process, or worker loop is started here.
    """
    validate_production_worker_config(config)
    if not callable(connection_factory):
        raise TrustedEngineeringWorkerHostError("postgres_connection_factory_required")
    if not callable(authenticated_workflow_authorizer):
        raise TrustedEngineeringWorkerHostError("authenticated_workflow_authorizer_required")
    if action_kernel is None or not callable(getattr(action_kernel, "execute", None)):
        raise TrustedEngineeringWorkerHostError("canonical_action_kernel_required")

    db = getattr(action_kernel, "db", None)
    if db is None or not callable(getattr(db, "table", None)):
        raise TrustedEngineeringWorkerHostError("durable_action_kernel_database_required")
    if getattr(action_kernel, "allow_in_memory", False):
        raise TrustedEngineeringWorkerHostError("in_memory_action_kernel_forbidden")

    try:
        worker_authorizer = build_council_action_kernel_worker_authorizer(
            action_kernel, event_store=event_store,
        )
        receipt_verifier = build_durable_action_receipt_verifier(db)
        dependencies = build_gateway_worker_dependencies(
            config=config,
            connection_factory=connection_factory,
            source_repository_path=source_repository_path,
            managed_workspace_root=managed_workspace_root,
            pinned_worker_image=pinned_worker_image,
            gateway=gateway,
            workflow_authorize=authenticated_workflow_authorizer,
            pipeline_authorize=worker_authorizer.authorize,
            admission_receipt_verifier=receipt_verifier,
            repair_capabilities_factory=repair_capabilities_factory,
            action_kernel=action_kernel,
            event_store=event_store,
            docker_binary=docker_binary,
            test_profile=test_profile,
            test_timeout_seconds=test_timeout_seconds,
            max_repair_attempts=max_repair_attempts,
            memory_limit=memory_limit,
            cpu_limit=cpu_limit,
            pids_limit=pids_limit,
        )
    except TrustedEngineeringWorkerHostError:
        raise
    except Exception as exc:
        raise TrustedEngineeringWorkerHostError(
            "trusted_worker_host_composition_failed_closed"
        ) from exc

    if dependencies.get("worker_authorizer") is not worker_authorizer:
        raise TrustedEngineeringWorkerHostError("worker_authorizer_identity_mismatch")
    if dependencies.get("admission_receipt_verifier") is not receipt_verifier:
        raise TrustedEngineeringWorkerHostError("canonical_receipt_verifier_identity_mismatch")

    def run(
        *,
        stopping: threading.Event,
        health_sink: Callable[[Mapping[str, Any]], None] | None = None,
        max_jobs: int | None = None,
    ) -> dict[str, Any]:
        """Start polling only when the dedicated host explicitly calls run()."""
        return run_production_engineering_worker(
            config=config,
            connection_factory=connection_factory,
            queue=dependencies["queue"],
            workflow=dependencies["workflow"],
            executor=dependencies["executor"],
            admission_receipt_verifier=dependencies["admission_receipt_verifier"],
            stopping=stopping,
            health_sink=health_sink,
            max_jobs=max_jobs,
        )

    return {
        **dependencies,
        "run": run,
        "worker_authorizer": worker_authorizer,
        "admission_receipt_verifier": receipt_verifier,
    }
