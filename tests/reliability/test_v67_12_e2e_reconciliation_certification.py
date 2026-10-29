import asyncio

from services.business_outcome_measurement import build_execution_observation
from services.execution_ledger import ExecutionLedger
from services.external_action_executor import ExternalActionExecutor
from services.v67_e2e_reconciliation_certification import (
    run_lost_response_reconciliation_scenario,
    run_rejection_before_commit_scenario,
    run_v67_12_certification,
)
from services.v67_external_side_effect_sandbox import SandboxProvider


def test_lost_response_is_reconciled_without_duplicate_side_effect():
    result = asyncio.run(run_lost_response_reconciliation_scenario())
    assert result.passed is True
    assert result.details["reconciliation"]["status"] == "committed"
    assert result.details["provider_committed_count"] == 1
    assert result.details["provider_call_count"] == 1


def test_execution_observation_does_not_promote_uncertainty_to_business_success():
    provider = SandboxProvider(default_failure_mode="timeout_after_commit")
    ledger = ExecutionLedger(allow_in_memory=True)
    executor = ExternalActionExecutor(ledger)
    result = asyncio.run(executor.execute(
        workspace_id="ws-v67-12",
        action="sandbox_external_side_effect",
        idempotency_key="obs-test",
        provider=provider.provider_name,
        operation=lambda: provider.submit(idempotency_key="obs-test", failure_mode="timeout_after_commit"),
    ))
    record = ledger._memory["obs-test"]
    observation = build_execution_observation(record)
    assert result["status"] == "uncertain"
    assert observation["outcome_state"] == "unknown"
    assert observation["business_success"] is None


def test_pre_commit_rejection_can_use_new_governed_retry_intent():
    result = asyncio.run(run_rejection_before_commit_scenario())
    assert result.passed is True
    assert result.details["first_execution"]["status"] == "failed"
    assert result.details["retry_execution"]["status"] == "succeeded"


def test_v67_12_certification_passes():
    result = run_v67_12_certification()
    assert result["status"] == "passed"
    assert all(item["passed"] for item in result["scenarios"])
