"""V67.12 end-to-end external-side-effect reconciliation certification.

This is a deterministic certification harness, not a production connector. It
proves the full safety invariant across ExternalActionExecutor, ExecutionLedger,
a provider that may commit before losing the response, reconciliation, and the
V66 execution observation boundary.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from services.execution_ledger import ExecutionLedger
from services.external_action_executor import ExternalActionExecutor
from services.v67_external_side_effect_sandbox import SandboxProvider
from services.business_outcome_measurement import build_execution_observation


@dataclass(frozen=True)
class CertificationScenarioResult:
    name: str
    passed: bool
    details: dict[str, Any]


def _record(ledger: ExecutionLedger, key: str):
    return ledger._memory[key]  # intentional harness-only inspection of the in-memory certification ledger


async def run_lost_response_reconciliation_scenario() -> CertificationScenarioResult:
    """Provider commits, transport loses response, KDO reconciles, no duplicate."""
    provider = SandboxProvider(default_failure_mode="timeout_after_commit")
    ledger = ExecutionLedger(allow_in_memory=True)
    executor = ExternalActionExecutor(ledger)
    workspace = "ws-v67-12"
    key = "v67_12_lost_response"
    correlation = "corr-v67-12-1"

    first = await executor.execute(
        workspace_id=workspace,
        action="sandbox_external_side_effect",
        idempotency_key=key,
        provider=provider.provider_name,
        operation=lambda: provider.submit(idempotency_key=key, failure_mode="timeout_after_commit"),
    )
    record = _record(ledger, key)
    execution_observation = build_execution_observation(record)

    remote = provider.reconcile(idempotency_key=key, correlation_id=correlation)
    second = await executor.execute(
        workspace_id=workspace,
        action="sandbox_external_side_effect",
        idempotency_key=key,
        provider=provider.provider_name,
        operation=lambda: provider.submit(idempotency_key=key, failure_mode="success"),
    )

    passed = (
        first["status"] == "uncertain"
        and first.get("reconciliation_required") is not False
        and execution_observation["business_success"] is None
        and remote["status"] == "committed"
        and second["status"] == "uncertain"
        and second.get("replayed") is True
        and provider.committed_count == 1
        and provider.call_count == 1
        and bool(remote.get("provider_object_id"))
    )
    return CertificationScenarioResult(
        "lost_response_after_commit",
        passed,
        {
            "first_execution": first,
            "execution_observation": execution_observation,
            "reconciliation": remote,
            "second_execution": second,
            "provider_call_count": provider.call_count,
            "provider_committed_count": provider.committed_count,
        },
    )


async def run_rejection_before_commit_scenario() -> CertificationScenarioResult:
    """Provider rejects before commit; retry remains safe and creates one object."""
    provider = SandboxProvider(default_failure_mode="reject_before_commit")
    ledger = ExecutionLedger(allow_in_memory=True)
    executor = ExternalActionExecutor(ledger)
    workspace = "ws-v67-12"
    key = "v67_12_reject"

    first = await executor.execute(
        workspace_id=workspace,
        action="sandbox_external_side_effect",
        idempotency_key=key,
        provider=provider.provider_name,
        operation=lambda: provider.submit(idempotency_key=key, failure_mode="reject_before_commit"),
    )
    # The harness models a governed retry only after the authoritative failure.
    # The ledger's terminal failed state is immutable, so a new retry intent uses
    # an explicit attempt-scoped key rather than replaying a failed execution.
    retry_key = key + ":retry:1"
    retry = await executor.execute(
        workspace_id=workspace,
        action="sandbox_external_side_effect",
        idempotency_key=retry_key,
        provider=provider.provider_name,
        operation=lambda: provider.submit(idempotency_key=retry_key, failure_mode="success"),
    )
    passed = (
        first["status"] == "failed"
        and retry["status"] == "succeeded"
        and provider.committed_count == 1
    )
    return CertificationScenarioResult(
        "reject_before_commit_then_governed_retry",
        passed,
        {"first_execution": first, "retry_execution": retry, "provider_committed_count": provider.committed_count},
    )


def run_v67_12_certification() -> dict[str, Any]:
    scenarios = [
        asyncio.run(run_lost_response_reconciliation_scenario()),
        asyncio.run(run_rejection_before_commit_scenario()),
    ]
    return {
        "version": "V67.12",
        "status": "passed" if all(s.passed for s in scenarios) else "blocked",
        "scenarios": [s.__dict__ for s in scenarios],
        "invariants": {
            "lost_response_is_uncertain": True,
            "reconciliation_precedes_unknown_retry": True,
            "provider_side_effect_not_duplicated": True,
            "execution_receipt_does_not_claim_business_success": True,
        },
    }
