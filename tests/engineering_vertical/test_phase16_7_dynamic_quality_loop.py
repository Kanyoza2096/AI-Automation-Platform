import pytest

from services.kdo_engineering_agent_registry import AgentRegistry
from services.kdo_engineering_phase16_7 import (
    DynamicQualityLoopError, run_dynamic_quality_loop,
)


def _registry(*, verification_id="reviewer-1", repair_patch="diff --git a/x b/x\n"):
    registry = AgentRegistry()
    registry.register_adapter("debugging_engineer", lambda assignment: {
        "agent_id": "debugger-1", "status": "succeeded", "summary": "Root cause identified",
        "evidence": {"root_cause": "reproduced failure"},
    })
    registry.register_adapter("backend_engineer", lambda assignment: {
        "agent_id": "repair-1", "status": "succeeded", "summary": "Proposed bounded patch",
        "evidence": {"patch_text": repair_patch},
    })
    registry.register_adapter("independent_reviewer", lambda assignment: {
        "agent_id": verification_id, "status": "succeeded", "summary": "Reviewed test evidence",
        "evidence": {"verification_status": "approved", "verification_id": "verify-123"},
    })
    return registry


def _task():
    return {"task_id": "task-1", "tenant_id": "tenant-1", "workspace_id": "workspace-1",
            "objective": "Fix API regression", "acceptance_criteria": ["Regression test passes"]}


def _auth(action, task):
    return True


def test_dynamic_loop_repairs_failed_test_then_requires_independent_verification():
    runs = [{"status": "failed", "exit_code": 1, "stderr": "assertion failed"},
            {"status": "passed", "exit_code": 0, "stdout": "1 passed"}]
    applied = []
    result = run_dynamic_quality_loop(task=_task(), registry=_registry(),
        test_runner=lambda i: runs[i], apply_patch=lambda patch: applied.append(patch) or {"status": "passed"},
        authorize=_auth, repair_capabilities={"backend", "services"}, max_repair_attempts=2)
    assert result.status == "awaiting_authorization"
    assert result.release_authorized is False
    assert result.attempts == 1
    assert len(result.test_runs) == 2
    assert applied and result.verification["independent_of_repair_agent"] is True
    assert len(result.evidence_fingerprint) == 64


def test_passing_baseline_does_not_apply_patch():
    applied = []
    result = run_dynamic_quality_loop(task=_task(), registry=_registry(),
        test_runner=lambda i: {"status": "passed", "exit_code": 0},
        apply_patch=lambda patch: applied.append(patch) or {"status": "passed"},
        authorize=_auth, repair_capabilities={"backend"})
    assert result.status == "awaiting_authorization"
    assert result.attempts == 0 and not applied and not result.repairs


def test_repair_cannot_self_verify():
    registry = _registry(verification_id="repair-1")
    runs = [{"status": "failed", "exit_code": 1}, {"status": "passed", "exit_code": 0}]
    result = run_dynamic_quality_loop(task=_task(), registry=registry,
        test_runner=lambda i: runs[i], apply_patch=lambda patch: {"status": "passed"},
        authorize=_auth, repair_capabilities={"backend"})
    assert result.status == "blocked"
    assert result.verification["independent_of_repair_agent"] is False


def test_failed_test_after_max_repairs_is_not_success():
    result = run_dynamic_quality_loop(task=_task(), registry=_registry(),
        test_runner=lambda i: {"status": "failed", "exit_code": 1},
        apply_patch=lambda patch: {"status": "passed"}, authorize=_auth,
        repair_capabilities={"backend"}, max_repair_attempts=1)
    assert result.status == "repair_exhausted"
    assert result.release_authorized is False
    assert len(result.test_runs) == 2


def test_authorization_denial_blocks_patch_application():
    calls = []
    def auth(action, task):
        return action != "apply_patch"
    result = run_dynamic_quality_loop(task=_task(), registry=_registry(),
        test_runner=lambda i: {"status": "failed", "exit_code": 1},
        apply_patch=lambda patch: calls.append(patch) or {"status": "passed"},
        authorize=auth, repair_capabilities={"backend"})
    assert result.status == "blocked"
    assert not calls


def test_invalid_task_and_retry_limits_fail_closed():
    with pytest.raises(DynamicQualityLoopError, match="acceptance_criteria_required"):
        run_dynamic_quality_loop(task={**_task(), "acceptance_criteria": []}, registry=_registry(),
            test_runner=lambda i: {"status": "passed", "exit_code": 0},
            apply_patch=lambda p: {"status": "passed"}, authorize=_auth, repair_capabilities={"backend"})
    with pytest.raises(DynamicQualityLoopError, match="max_repair_attempts"):
        run_dynamic_quality_loop(task=_task(), registry=_registry(),
            test_runner=lambda i: {"status": "passed", "exit_code": 0},
            apply_patch=lambda p: {"status": "passed"}, authorize=_auth,
            repair_capabilities={"backend"}, max_repair_attempts=99)


def test_missing_runtime_capabilities_fail_closed():
    with pytest.raises(DynamicQualityLoopError, match="capabilities_unavailable"):
        run_dynamic_quality_loop(task=_task(), registry=AgentRegistry(),
            test_runner=lambda i: {"status": "passed", "exit_code": 0},
            apply_patch=lambda p: {"status": "passed"}, authorize=_auth, repair_capabilities={"backend"})


def test_workspace_runner_adapter_executes_allowlisted_command(tmp_path):
    from services.kdo_engineering_phase16_7 import make_workspace_test_runner
    runner = make_workspace_test_runner(str(tmp_path), ["python", "-c", "print('kdo-test-ok')"])
    result = runner(0)
    assert result["status"] == "passed" and result["exit_code"] == 0
    assert "kdo-test-ok" in result["stdout"]


def test_dynamic_executor_wraps_existing_build_and_uses_current_registry(tmp_path):
    from types import SimpleNamespace
    from services.kdo_engineering_phase16_7 import make_dynamic_quality_executor
    registry = _registry()
    task = SimpleNamespace(task_id="task-1", tenant_id="tenant-1", workspace_id="workspace-1",
        objective="Run an engineering build", acceptance_criteria=("Tests pass",))
    calls = []
    executor = make_dynamic_quality_executor(
        build_executor=lambda path, contract, steps: calls.append("build") or {"build_status": "completed"},
        registry_factory=lambda contract, path, steps: registry,
        test_command_factory=lambda contract, path, steps: ["python", "-c", "print('ok')"],
        repair_capabilities_factory=lambda contract, steps: {"backend", "services"},
        authorize=_auth, max_repair_attempts=1)
    output = executor(str(tmp_path), task, ("implement feature",))
    assert calls == ["build"]
    assert output["build_status"] == "completed"
    assert output["quality_loop"]["status"] == "awaiting_authorization"
    assert output["quality_loop"]["release_authorized"] is False
