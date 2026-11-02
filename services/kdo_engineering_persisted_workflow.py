"""Persisted task lifecycle integration for KDO's engineering vertical.

This layer wires the actual SQLite task and artifact repository contracts together.
It still requires an external authorization callback; it does not claim to be the
authoritative KDO Action Kernel or execution ledger.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from services.kdo_engineering_vertical_contracts import (
    EngineeringTaskContract,
    EngineeringTaskStatus,
)
from services.kdo_engineering_task_repository import (
    SQLiteEngineeringTaskRepository,
    EngineeringTaskConflict,
    EngineeringTaskNotFound,
)
from services.kdo_engineering_artifacts import SQLiteEngineeringArtifactRepository
from services.kdo_engineering_orchestrator import (
    EngineeringRunResult,
    OrchestrationError,
    run_engineering_task,
)


def run_persisted_engineering_task(
    *,
    task_contract: EngineeringTaskContract,
    task_repository: SQLiteEngineeringTaskRepository,
    artifact_repository: SQLiteEngineeringArtifactRepository,
    repository_path: str | Path,
    patch_text: str,
    test_command: list[str],
    authorize: Callable[[str, Mapping[str, Any]], bool],
    timeout_seconds: int = 60,
) -> EngineeringRunResult:
    """Persist lifecycle transitions and evidence around one bounded engineering run.

    Accepts only PLANNED or QUEUED tasks; callers must explicitly plan/queue tasks.
    It performs CAS transitions and uses the concrete Phase 3 artifact repository.
    """
    stored = task_repository.get(
        task_contract.task_id, tenant_id=task_contract.tenant_id,
        workspace_id=task_contract.workspace_id,
    )
    if stored.fingerprint() != task_contract.fingerprint():
        raise EngineeringTaskConflict("supplied_task_does_not_match_persisted_revision")
    if stored.status not in {EngineeringTaskStatus.PLANNED, EngineeringTaskStatus.QUEUED}:
        raise OrchestrationError("task_must_be_planned_or_queued_before_execution")

    # Queue a planned task first, then atomically claim it as running using revision CAS.
    if stored.status == EngineeringTaskStatus.PLANNED:
        stored = task_repository.transition(
            stored.task_id, EngineeringTaskStatus.QUEUED,
            tenant_id=stored.tenant_id, workspace_id=stored.workspace_id,
            expected_revision=stored.revision,
        )
    running = task_repository.transition(
        stored.task_id, EngineeringTaskStatus.RUNNING,
        tenant_id=stored.tenant_id, workspace_id=stored.workspace_id,
        expected_revision=stored.revision,
    )

    task_payload = {
        "task_id": running.task_id,
        "tenant_id": running.tenant_id,
        "workspace_id": running.workspace_id,
        "objective": running.objective,
        "acceptance_criteria": list(running.acceptance_criteria),
        "revision": running.revision,
    }
    result = run_engineering_task(
        task=task_payload,
        repository_path=repository_path,
        patch_text=patch_text,
        test_command=test_command,
        authorize=authorize,
        artifact_store=artifact_repository,
        timeout_seconds=timeout_seconds,
    )

    evidence = {
        "engineering_run_id": result.run_id,
        "baseline_revision": result.revision_before,
        "final_revision": result.revision_after,
        "patch_digest": result.patch_digest,
        "test_run_id": result.run_id if result.test_exit_code is not None else None,
        "tests_passed": result.status == "awaiting_review" and result.test_exit_code == 0,
        "authorization_checks": list(result.evidence.get("authorization_checks", [])),
        "reason": result.reason,
    }
    if result.status == "awaiting_review":
        next_status = EngineeringTaskStatus.AWAITING_REVIEW
    elif result.status == "awaiting_approval":
        next_status = EngineeringTaskStatus.BLOCKED
        evidence["block_reason"] = "test_execution_authorization_required"
    elif result.status == "blocked":
        next_status = EngineeringTaskStatus.BLOCKED
    else:
        next_status = EngineeringTaskStatus.FAILED

    task_repository.transition(
        running.task_id, next_status,
        tenant_id=running.tenant_id, workspace_id=running.workspace_id,
        expected_revision=running.revision, evidence=evidence,
    )
    return result



def recover_interrupted_engineering_tasks(
    *, task_repository: SQLiteEngineeringTaskRepository, tenant_id: str, workspace_id: str,
    worker_is_alive: Callable[[EngineeringTaskContract], bool], limit: int = 100,
) -> list[EngineeringTaskContract]:
    """Block confirmed orphaned RUNNING tasks using revision compare-and-swap.

    Unknown liveness is treated as alive. This deliberately does not resume work
    automatically because a previous worker may have modified a workspace or
    started a test process before it stopped.
    """
    candidates = task_repository.list_status(
        EngineeringTaskStatus.RUNNING, tenant_id=tenant_id, workspace_id=workspace_id, limit=limit
    )
    recovered: list[EngineeringTaskContract] = []
    for task in candidates:
        try:
            alive = worker_is_alive(task)
        except Exception:
            continue  # A failed probe is not evidence that the worker is absent.
        if alive is not False:
            continue
        try:
            updated = task_repository.transition(
                task.task_id, EngineeringTaskStatus.BLOCKED,
                tenant_id=tenant_id, workspace_id=workspace_id,
                expected_revision=task.revision,
                evidence={"recovery": {
                    "status": "interrupted_worker_confirmed_absent",
                    "automatic_resume": False,
                    "cleanup_required": True,
                    "reason": "worker_absence_confirmed; inspect workspace and artifacts before retry",
                }},
            )
            recovered.append(updated)
        except EngineeringTaskConflict:
            continue  # Concurrent state change requires reconciliation.
    return recovered

# Phase 16.2's approved isolated-worktree workflow is exposed here as the safe
# orchestration entry point for callers migrating from the legacy patch runner.
# Importing it does not grant execution authority or enable release actions.
from services.kdo_engineering_phase16_2 import (  # noqa: E402
    KDOPhase162Workflow,
    Phase162Error,
    SQLiteEngineeringWorkspaceRepository,
)

# Phase 16.3's Council/Action-Kernel build admission and lease-fenced worker queue.
# The queue must be explicitly selected and wired at application bootstrap.
from services.kdo_engineering_phase16_3 import (  # noqa: E402
    EngineeringBuildJob,
    Phase163Error,
    SQLiteEngineeringBuildQueue,
    admit_approved_build,
    register_engineering_build_enqueue_contract,
    run_claimed_build_job,
)


def claim_tenant_scoped_engineering_build_job(
    *, queue: Any, worker_id: str, tenant_id: str, workspace_id: str,
    lease_seconds: int = 300,
) -> EngineeringBuildJob | None:
    """Worker-facing claim entry point; tenant-bound workers cannot omit scope.

    The queue must be an explicitly configured Phase 16.3 SQLite or Phase 16.4
    shared PostgreSQL adapter. This function claims only; it does not authorize
    execution, run code, or grant release authority.
    """
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise Phase163Error("tenant_and_workspace_scope_required")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise Phase163Error("tenant_and_workspace_scope_required")
    claim = getattr(queue, "claim_next", None)
    if not callable(claim):
        raise Phase163Error("engineering_build_queue_claim_api_required")
    return claim(
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
