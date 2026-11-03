"""Phase 16.7: dynamic, bounded test-diagnose-repair-verify orchestration.

This layer reuses the existing AgentRegistry and registered adapters. It does not
create a fixed permanent agent roster, execute model-generated code directly, or
grant release authority. The host injects the test runner, patch applier, and
policy authorization callback; those must operate in an isolated workspace.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Callable, Mapping, Iterable
import hashlib
import json
import threading

from services.kdo_engineering_agent_team import AgentAssignment
from services.kdo_engineering_agent_registry import AgentRegistry
from services.kdo_engineering_runner import run_command, RunnerInputError
from services.kdo_engineering_repository import apply_patch as apply_repository_patch, RepositoryInputError


class DynamicQualityLoopError(RuntimeError):
    """Invalid task, missing runtime capability, or unsafe orchestration request."""


@dataclass(frozen=True)
class QualityLoopResult:
    task_id: str
    status: str
    selected_roles: tuple[str, ...]
    attempts: int
    test_runs: tuple[Mapping[str, Any], ...]
    repairs: tuple[Mapping[str, Any], ...]
    verification: Mapping[str, Any] | None
    evidence_fingerprint: str
    release_authorized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "status": self.status,
            "selected_roles": list(self.selected_roles), "attempts": self.attempts,
            "test_runs": [dict(x) for x in self.test_runs],
            "repairs": [dict(x) for x in self.repairs],
            "verification": dict(self.verification) if self.verification else None,
            "evidence_fingerprint": self.evidence_fingerprint,
            "release_authorized": False,
        }


def _valid_agent_output(value: Any) -> tuple[str, str, dict[str, Any]] | None:
    if not isinstance(value, Mapping):
        return None
    agent_id = value.get("agent_id")
    status = value.get("status")
    summary = value.get("summary")
    evidence = value.get("evidence", {})
    if not isinstance(agent_id, str) or not agent_id.strip():
        return None
    if status != "succeeded" or not isinstance(summary, str) or not summary.strip():
        return None
    if not isinstance(evidence, Mapping):
        return None
    return agent_id.strip(), summary.strip(), dict(evidence)


def _choose_role(registry: AgentRegistry, capability_sets: Iterable[Iterable[str]], *, exclude: set[str] | None = None) -> tuple[str, Callable] | None:
    """Choose an installed adapter by capability, not by a fixed agent roster."""
    excluded = exclude or set()
    roles = registry.roles()
    for wanted in capability_sets:
        wanted_set = frozenset(wanted)
        candidates = [r for r in roles if r.role_id not in excluded and r.capabilities & wanted_set]
        for role in candidates:
            adapter = registry.adapter(role.role_id)
            if callable(adapter):
                return role.role_id, adapter
    return None


def run_dynamic_quality_loop(
    *, task: Mapping[str, Any], registry: AgentRegistry,
    test_runner: Callable[[int], Mapping[str, Any]],
    apply_patch: Callable[[str], Mapping[str, Any]],
    authorize: Callable[[str, Mapping[str, Any]], bool],
    repair_capabilities: Iterable[str],
    max_repair_attempts: int = 2,
    cancellation_event: threading.Event | None = None,
    diagnostic_capability_sets: Iterable[Iterable[str]] = (("debugging", "root_cause"), ("diagnostics",)),
    verification_capability_sets: Iterable[Iterable[str]] = (("independent_review", "review"), ("verification",)),
) -> QualityLoopResult:
    """Run test -> diagnose -> propose patch -> apply -> retest -> verify.

    Required task keys: task_id, tenant_id, workspace_id, objective and
    acceptance_criteria (non-empty sequence). Agent adapters only propose; the
    injected host patch applier decides how to validate/apply patches. The exact
    production Action Kernel must be connected through ``authorize``.
    """
    required = ("task_id", "tenant_id", "workspace_id", "objective")
    if any(not isinstance(task.get(k), str) or not task[k].strip() for k in required):
        raise DynamicQualityLoopError("task_scope_and_objective_required")
    criteria = task.get("acceptance_criteria")
    if not isinstance(criteria, (list, tuple)) or not criteria or any(not isinstance(x, str) or not x.strip() for x in criteria):
        raise DynamicQualityLoopError("acceptance_criteria_required")
    if not isinstance(registry, AgentRegistry):
        raise DynamicQualityLoopError("existing_agent_registry_required")
    if not callable(test_runner) or not callable(apply_patch) or not callable(authorize):
        raise DynamicQualityLoopError("test_runner_patch_applier_and_authorizer_required")
    if not isinstance(max_repair_attempts, int) or not 0 <= max_repair_attempts <= 5:
        raise DynamicQualityLoopError("max_repair_attempts_must_be_between_0_and_5")
    repair_caps = frozenset(repair_capabilities)
    if not repair_caps or any(not isinstance(x, str) or not x.strip() for x in repair_caps):
        raise DynamicQualityLoopError("repair_capabilities_required")

    diagnostic = _choose_role(registry, diagnostic_capability_sets)
    repair = _choose_role(registry, (repair_caps,))
    verifier = _choose_role(registry, verification_capability_sets,
                             exclude={x for x in (repair[0] if repair else None, diagnostic[0] if diagnostic else None) if x})
    if diagnostic is None or repair is None or verifier is None:
        raise DynamicQualityLoopError("required_installed_dynamic_capabilities_unavailable")
    selected_roles = (diagnostic[0], repair[0], verifier[0])
    if len(set(selected_roles)) != len(selected_roles):
        # Distinct runtime identities are checked again after invocation; avoid role aliasing here too.
        raise DynamicQualityLoopError("diagnosis_repair_verification_roles_must_be_distinct")

    evidence_base = {"task_id": task["task_id"], "tenant_id": task["tenant_id"],
                     "workspace_id": task["workspace_id"], "objective": task["objective"],
                     "acceptance_criteria": list(criteria), "selected_roles": list(selected_roles)}
    test_runs: list[Mapping[str, Any]] = []
    repairs: list[Mapping[str, Any]] = []
    verification: Mapping[str, Any] | None = None
    repair_agent_id: str | None = None
    status = "blocked"
    attempts = 0

    def cancelled() -> bool:
        return cancellation_event is not None and cancellation_event.is_set()

    def allowed(action: str) -> bool:
        if cancelled():
            return False
        try:
            return authorize(action, task) is True
        except Exception:
            return False

    def run_test(index: int) -> Mapping[str, Any] | None:
        if not allowed("run_tests"):
            return None
        try:
            result = test_runner(index)
        except Exception as exc:
            return {"status": "runner_error", "exit_code": None, "error_type": type(exc).__name__,
                    "iteration": index, "authoritative": True}
        if not isinstance(result, Mapping):
            return {"status": "invalid_runner_result", "exit_code": None, "iteration": index,
                    "authoritative": True}
        normalized = dict(result)
        normalized["iteration"] = index
        # A successful result requires a concrete exit code of zero from the host runner.
        normalized["authoritative"] = True
        if normalized.get("status") != "passed" or normalized.get("exit_code") != 0:
            normalized["status"] = "failed" if normalized.get("status") == "passed" else normalized.get("status", "failed")
        return normalized

    initial = run_test(0)
    if cancelled() or (initial is not None and initial.get("status") == "cancelled"):
        status = "cancelled"
        test_runs.append(dict(initial) if initial is not None else {"status": "cancelled", "iteration": 0})
    elif initial is None:
        status = "blocked"
        test_runs.append({"status": "blocked", "reason": "test_execution_not_authorized", "iteration": 0})
    else:
        test_runs.append(initial)
        if initial.get("status") == "passed" and initial.get("exit_code") == 0:
            attempts = 0
            status = "verification_pending"
        else:
            for attempt in range(1, max_repair_attempts + 1):
                attempts = attempt
                if cancelled():
                    status = "cancelled"
                    break
                if not allowed("diagnose_failure"):
                    status = "blocked"
                    repairs.append({"attempt": attempt, "status": "blocked", "reason": "diagnosis_not_authorized"})
                    break
                diagnosis_assignment = AgentAssignment(task["task_id"], diagnostic[0],
                    "Diagnose the current failing test run; provide a root-cause hypothesis and evidence, not a release decision.",
                    {**evidence_base, "failed_test_run": dict(test_runs[-1]), "attempt": attempt}, ())
                try:
                    diagnosis_raw = diagnostic[1](diagnosis_assignment)
                except Exception as exc:
                    diagnosis_raw = {"status": "failed", "evidence": {"error_type": type(exc).__name__}}
                diagnosis = _valid_agent_output(diagnosis_raw)
                if diagnosis is None:
                    repairs.append({"attempt": attempt, "status": "blocked", "reason": "diagnosis_output_invalid"})
                    status = "blocked"
                    break
                diagnostic_id, diagnosis_summary, diagnosis_evidence = diagnosis
                if not allowed("propose_repair"):
                    repairs.append({"attempt": attempt, "status": "blocked", "reason": "repair_proposal_not_authorized"})
                    status = "blocked"
                    break
                repair_assignment = AgentAssignment(task["task_id"], repair[0],
                    "Propose the smallest patch for the reproduced defect. Return patch_text in evidence; do not claim tests passed.",
                    {**evidence_base, "failed_test_run": dict(test_runs[-1]),
                     "diagnosis": {"agent_id": diagnostic_id, "summary": diagnosis_summary,
                                   "evidence": diagnosis_evidence}, "attempt": attempt}, (diagnostic[0],))
                try:
                    repair_raw = repair[1](repair_assignment)
                except Exception as exc:
                    repair_raw = {"status": "failed", "evidence": {"error_type": type(exc).__name__}}
                proposed = _valid_agent_output(repair_raw)
                if proposed is None:
                    repairs.append({"attempt": attempt, "status": "blocked", "reason": "repair_output_invalid"})
                    status = "blocked"
                    break
                current_repair_id, repair_summary, repair_evidence = proposed
                patch_text = repair_evidence.get("patch_text")
                if not isinstance(patch_text, str) or not patch_text.strip():
                    repairs.append({"attempt": attempt, "status": "blocked", "repair_agent_id": current_repair_id,
                                    "reason": "patch_text_missing"})
                    status = "blocked"
                    break
                if cancelled():
                    repairs.append({"attempt": attempt, "status": "cancelled", "reason": "execution_cancelled"})
                    status = "cancelled"
                    break
                if not allowed("apply_patch"):
                    repairs.append({"attempt": attempt, "status": "blocked", "repair_agent_id": current_repair_id,
                                    "reason": "patch_application_not_authorized"})
                    status = "blocked"
                    break
                try:
                    applied = apply_patch(patch_text)
                except Exception as exc:
                    applied = {"status": "failed", "error_type": type(exc).__name__}
                if not isinstance(applied, Mapping) or applied.get("status") != "passed":
                    repairs.append({"attempt": attempt, "status": "failed", "repair_agent_id": current_repair_id,
                                    "reason": "patch_application_failed", "apply_result": dict(applied) if isinstance(applied, Mapping) else {}})
                    status = "blocked"
                    break
                repair_agent_id = current_repair_id
                patch_digest = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
                repairs.append({"attempt": attempt, "status": "applied", "repair_agent_id": current_repair_id,
                                "summary": repair_summary, "patch_digest": patch_digest,
                                "apply_result": dict(applied), "diagnosis_agent_id": diagnostic_id})
                rerun = run_test(attempt)
                if rerun is None:
                    test_runs.append({"status": "blocked", "reason": "test_execution_not_authorized", "iteration": attempt})
                    status = "blocked"
                    break
                test_runs.append(rerun)
                if rerun.get("status") == "passed" and rerun.get("exit_code") == 0:
                    status = "verification_pending"
                    break
            else:
                status = "repair_exhausted"

    if status == "verification_pending" and cancelled():
        status = "cancelled"
    if status == "verification_pending":
        if not allowed("independent_verify"):
            status = "blocked"
            verification = {"status": "blocked", "reason": "verification_not_authorized"}
        else:
            final_test = dict(test_runs[-1]) if test_runs else {}
            verify_assignment = AgentAssignment(task["task_id"], verifier[0],
                "Independently verify the exact current revision using the supplied authoritative test evidence. Do not authorize release.",
                {**evidence_base, "test_runs": [dict(x) for x in test_runs],
                 "repairs": [dict(x) for x in repairs], "final_test": final_test},
                tuple([repair[0]] if repairs else []))
            try:
                verify_raw = verifier[1](verify_assignment)
            except Exception as exc:
                verify_raw = {"status": "failed", "evidence": {"error_type": type(exc).__name__}}
            verified = _valid_agent_output(verify_raw)
            if verified is None:
                status = "blocked"
                verification = {"status": "blocked", "reason": "verification_output_invalid"}
            else:
                verifier_id, verify_summary, verify_evidence = verified
                approved = verify_evidence.get("verification_status") == "approved" and bool(verify_evidence.get("verification_id"))
                identity_independent = verifier_id != repair_agent_id
                tests_passed = final_test.get("status") == "passed" and final_test.get("exit_code") == 0
                verification = {"status": "approved" if approved and identity_independent and tests_passed else "rejected",
                    "agent_id": verifier_id, "summary": verify_summary, "evidence": verify_evidence,
                    "independent_of_repair_agent": identity_independent, "authoritative_tests_passed": tests_passed}
                status = "awaiting_authorization" if approved and identity_independent and tests_passed else "blocked"

    serial = json.dumps({"base": evidence_base, "status": status, "attempts": attempts,
        "test_runs": test_runs, "repairs": repairs, "verification": verification,
        "release_authorized": False}, sort_keys=True, separators=(",", ":"), default=str)
    return QualityLoopResult(task["task_id"], status, selected_roles, attempts,
        tuple(test_runs), tuple(repairs), verification,
        hashlib.sha256(serial.encode("utf-8")).hexdigest(), False)


def make_workspace_test_runner(workspace_path: str, command: Iterable[str], *, timeout_seconds: int = 60) -> Callable[[int], Mapping[str, Any]]:
    """Adapt KDO's bounded allowlisted runner to the quality-loop test contract.

    The workspace must already be isolated by the host worker/container. The
    runner does not provide OS-level sandboxing or release authority.
    """
    command_list = list(command)
    if not command_list:
        raise DynamicQualityLoopError("test_command_required")
    def run(iteration: int) -> Mapping[str, Any]:
        result = run_command(workspace_path, command_list, timeout_seconds=timeout_seconds)
        return {"status": result.status, "exit_code": result.exit_code,
                "duration_seconds": result.duration_seconds, "stdout": result.stdout,
                "stderr": result.stderr, "timed_out": result.timed_out,
                "command": list(result.command), "iteration": iteration}
    return run


def make_workspace_patch_applier(workspace_path: str) -> Callable[[str], Mapping[str, Any]]:
    """Adapt KDO's checked Git patch primitive for a disposable worktree."""
    def apply(patch_text: str) -> Mapping[str, Any]:
        result = apply_repository_patch(workspace_path, patch_text)
        return {"status": result.status, "exit_code": result.exit_code,
                "stdout": result.stdout, "stderr": result.stderr, "truncated": result.truncated}
    return apply


def make_dynamic_quality_executor(*, build_executor: Callable[..., Mapping[str, Any]],
        registry_factory: Callable[[Any, str, tuple[str, ...]], AgentRegistry],
        test_command_factory: Callable[[Any, str, tuple[str, ...]], Iterable[str]],
        repair_capabilities_factory: Callable[[Any, tuple[str, ...]], Iterable[str]],
        authorize: Callable[[str, Mapping[str, Any]], bool],
        max_repair_attempts: int = 2, test_timeout_seconds: int = 60,
        test_runner_factory: Callable[..., Callable[[int], Mapping[str, Any]]] | None = None) -> Callable[..., Mapping[str, Any]]:
    """Wrap an existing Phase 16.2 build executor with dynamic QA and repair.

    Pass the returned callable as ``executor=`` to ``KDOPhase162Workflow``. The
    existing build step runs first in its task worktree; then the current registry
    selects installed adapters by task capabilities and the bounded quality loop
    tests/repairs the exact workspace. Missing adapters or denied policy block.
    """
    if not all(callable(x) for x in (build_executor, registry_factory, test_command_factory,
                                     repair_capabilities_factory, authorize)):
        raise DynamicQualityLoopError("dynamic_quality_executor_dependencies_required")
    if not isinstance(max_repair_attempts, int) or not 0 <= max_repair_attempts <= 5:
        raise DynamicQualityLoopError("max_repair_attempts_must_be_between_0_and_5")
    def execute(workspace_path: str, task_contract: Any, steps: tuple[str, ...], *, cancellation_event: threading.Event | None = None) -> Mapping[str, Any]:
        build_output = build_executor(workspace_path, task_contract, steps)
        if not isinstance(build_output, Mapping):
            raise DynamicQualityLoopError("build_executor_must_return_mapping")
        task_data = {"task_id": task_contract.task_id, "tenant_id": task_contract.tenant_id,
                     "workspace_id": task_contract.workspace_id, "objective": task_contract.objective,
                     "acceptance_criteria": list(task_contract.acceptance_criteria)}
        registry = registry_factory(task_contract, workspace_path, steps)
        if not isinstance(registry, AgentRegistry):
            raise DynamicQualityLoopError("registry_factory_must_return_existing_agent_registry")
        capabilities = repair_capabilities_factory(task_contract, steps)
        if test_runner_factory is not None:
            # A sandboxed runner owns command selection; do not even ask the legacy
            # host-command factory to construct a command in this execution mode.
            test_runner = test_runner_factory(workspace_path, task_contract, steps, cancellation_event=cancellation_event)
        else:
            command = test_command_factory(task_contract, workspace_path, steps)
            test_runner = make_workspace_test_runner(workspace_path, command, timeout_seconds=test_timeout_seconds)
        if not callable(test_runner):
            raise DynamicQualityLoopError("test_runner_factory_must_return_callable")
        quality = run_dynamic_quality_loop(task=task_data, registry=registry,
            test_runner=test_runner,
            apply_patch=make_workspace_patch_applier(workspace_path), authorize=authorize,
            repair_capabilities=capabilities, max_repair_attempts=max_repair_attempts,
            cancellation_event=cancellation_event)
        return {**dict(build_output), "quality_loop": quality.to_dict()}
    return execute
