"""Bounded orchestration for KDO engineering tasks.

This module composes the task repository, Git adapter, artifact store, and local runner.
It intentionally requires an injected authorization callback before mutations or execution.
It does not pretend that an injected callback is the authoritative KDO Action Kernel; production
integration must supply and test the real policy/ledger adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Any
import hashlib
import json
import time
import uuid

from services.kdo_engineering_repository import inspect_repository, apply_patch, reverse_patch, RepositoryInputError
from services.kdo_engineering_runner import run_command, RunnerInputError


class OrchestrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class EngineeringRunResult:
    run_id: str
    task_id: str
    status: str
    revision_before: str | None
    revision_after: str | None
    patch_digest: str | None
    test_exit_code: int | None
    evidence: Mapping[str, Any]
    reason: str | None = None


def _artifact_call(artifact_store: Any, task_id: str, workspace_id: str,
                   kind: str, content: str, metadata: dict[str, Any], *,
                   tenant_id: str, base_revision: str | None = None) -> Any:
    """Persist evidence through the actual Phase 3 SQLite artifact repository API."""
    if artifact_store is None:
        return None
    method = getattr(artifact_store, "put", None)
    if method is None:
        raise OrchestrationError("artifact_store must expose put")
    kind_map = {
        "repository_snapshot": ("repository_snapshot", "repository-snapshot.json"),
        "patch": ("patch", "proposed.patch"),
        "test_log": ("test_log", "test-log.json"),
        "build_log": ("build_log", "build-log.json"),
        "review_report": ("review_report", "review-report.json"),
    }
    if kind not in kind_map:
        raise OrchestrationError(f"unsupported artifact kind: {kind}")
    artifact_kind, filename = kind_map[kind]
    if kind == "repository_snapshot" and not base_revision:
        raise OrchestrationError("repository snapshot requires base_revision")
    raw = content.encode("utf-8")
    return method(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        task_id=task_id,
        kind=artifact_kind,
        filename=filename,
        content=raw,
        base_revision=base_revision,
        metadata=metadata,
    )

def run_engineering_task(
    *,
    task: Mapping[str, Any],
    repository_path: str | Path,
    patch_text: str,
    test_command: list[str],
    authorize: Callable[[str, Mapping[str, Any]], bool],
    artifact_store: Any = None,
    timeout_seconds: int = 60,
) -> EngineeringRunResult:
    """Run a single proposed patch with authorization gates and evidence capture.

    Required task keys: task_id, workspace_id, tenant_id.
    `authorize` is invoked once before patch mutation and once before test execution.
    The callback must be connected to the authoritative policy system by production wiring.
    This function does not commit, push, or deploy.
    """
    for key in ("task_id", "workspace_id", "tenant_id"):
        if not isinstance(task.get(key), str) or not task[key].strip():
            raise OrchestrationError(f"task requires non-empty {key}")

    task_id = task["task_id"]
    workspace_id = task["workspace_id"]
    run_id = str(uuid.uuid4())
    evidence: dict[str, Any] = {"authorization_checks": [], "artifacts": []}
    before = None
    after = None
    patch_digest = hashlib.sha256(patch_text.encode("utf-8")).hexdigest() if isinstance(patch_text, str) else None
    test_exit = None

    def gate(action: str) -> bool:
        try:
            allowed = authorize(action, task)
        except Exception as exc:
            evidence["authorization_checks"].append({"action": action, "allowed": False, "error": type(exc).__name__})
            return False
        evidence["authorization_checks"].append({"action": action, "allowed": allowed is True})
        return allowed is True

    try:
        snapshot = inspect_repository(repository_path)
        if snapshot["revision"].status != "passed":
            return EngineeringRunResult(run_id, task_id, "blocked", None, None, patch_digest, None,
                                        evidence, "Could not establish baseline repository revision")
        before = snapshot["revision"].stdout.strip()
        evidence["baseline_status"] = snapshot["status"].stdout
        evidence["baseline_revision"] = before
        _artifact_call(artifact_store, task_id, workspace_id, "repository_snapshot",
                       json.dumps({"status": evidence["baseline_status"], "revision": before}, sort_keys=True),
                       {"run_id": run_id, "tenant_id": task["tenant_id"]},
                       tenant_id=task["tenant_id"], base_revision=before)

        if not gate("apply_patch"):
            return EngineeringRunResult(run_id, task_id, "blocked", before, before, patch_digest, None,
                                        evidence, "Patch mutation was not authorized")
        patched = apply_patch(repository_path, patch_text)
        evidence["patch_result"] = {"status": patched.status, "exit_code": patched.exit_code,
                                    "stderr": patched.stderr}
        if patched.status != "passed":
            return EngineeringRunResult(run_id, task_id, "failed", before, before, patch_digest, None,
                                        evidence, "Patch could not be applied")
        evidence["artifacts"].append("patch")
        _artifact_call(artifact_store, task_id, workspace_id, "patch", patch_text,
                       {"run_id": run_id, "tenant_id": task["tenant_id"], "sha256": patch_digest},
                       tenant_id=task["tenant_id"], base_revision=before)

        if not gate("run_tests"):
            # Reverse only the patch applied by this run; preserve unrelated changes.
            cleanup = reverse_patch(repository_path, patch_text)
            evidence["cleanup"] = {
                "attempted": True, "status": cleanup.status,
                "exit_code": cleanup.exit_code, "stderr": cleanup.stderr,
                "required_manual_cleanup": cleanup.status != "passed",
            }
            if cleanup.status == "passed":
                return EngineeringRunResult(run_id, task_id, "awaiting_approval", before, before,
                                            patch_digest, None, evidence,
                                            "Test execution was not authorized; applied patch was reversed")
            return EngineeringRunResult(run_id, task_id, "blocked", before, before, patch_digest, None,
                                        evidence, "Test execution was not authorized and patch reversal failed; workspace cleanup required")

        try:
            test = run_command(repository_path, test_command, timeout_seconds=timeout_seconds)
        except RunnerInputError as exc:
            return EngineeringRunResult(run_id, task_id, "failed", before, before, patch_digest, None,
                                        evidence, f"Invalid test command: {exc}")
        test_exit = test.exit_code
        evidence["test_result"] = {
            "status": test.status, "exit_code": test.exit_code, "duration_seconds": test.duration_seconds,
            "stdout": test.stdout, "stderr": test.stderr, "timed_out": test.timed_out,
        }
        _artifact_call(artifact_store, task_id, workspace_id, "test_log",
                       json.dumps(evidence["test_result"], sort_keys=True),
                       {"run_id": run_id, "tenant_id": task["tenant_id"]},
                       tenant_id=task["tenant_id"], base_revision=before)

        current = inspect_repository(repository_path)
        if current["revision"].status == "passed":
            after = current["revision"].stdout.strip()
        evidence["final_status"] = current["status"].stdout
        evidence["final_diff"] = current["diff"].stdout
        _artifact_call(artifact_store, task_id, workspace_id, "review_report",
                       json.dumps({"baseline_revision": before, "final_revision": after,
                                   "patch_digest": patch_digest, "test_exit_code": test_exit,
                                   "final_status": evidence["final_status"]}, sort_keys=True),
                       {"run_id": run_id, "tenant_id": task["tenant_id"]},
                       tenant_id=task["tenant_id"], base_revision=before)

        if test.status != "passed" or test.exit_code != 0:
            status = "failed"
            reason = "Tests did not pass"
        else:
            # Passing tests alone do not equal a production-verified completion or receipt.
            status = "awaiting_review"
            reason = "Tests passed; independent review and authoritative execution receipt are still required"
        return EngineeringRunResult(run_id, task_id, status, before, after, patch_digest,
                                    test_exit, evidence, reason)
    except (RepositoryInputError, OrchestrationError) as exc:
        return EngineeringRunResult(run_id, task_id, "failed", before, after, patch_digest, test_exit,
                                    evidence, str(exc))
