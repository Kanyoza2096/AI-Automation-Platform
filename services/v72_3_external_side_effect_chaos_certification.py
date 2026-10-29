"""C8.3 external side-effect chaos certification.

Deterministic pre-production certification for the distributed-system boundary
between KDO and an external provider.  It deliberately does not claim a live
provider was tested.  The harness proves that the execution layer treats
ambiguous provider outcomes conservatively and relies on provider-side
idempotency/reconciliation rather than blind replay.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from services.execution_ledger import ExecutionLedger
from services.external_action_executor import ExternalActionExecutor
from services.v67_external_side_effect_sandbox import SandboxProvider


@dataclass(frozen=True)
class ChaosScenario:
    name: str
    passed: bool
    invariant: str
    evidence: dict[str, Any]


def _execute(provider: SandboxProvider, key: str, mode: str, workspace: str = "ws-c83") -> dict[str, Any]:
    ledger = ExecutionLedger(allow_in_memory=True)
    executor = ExternalActionExecutor(ledger)
    return asyncio.run(executor.execute(
        workspace_id=workspace,
        action="sandbox.c83.external_side_effect",
        idempotency_key=key,
        provider=provider.provider_name,
        operation=lambda: provider.submit(idempotency_key=key, failure_mode=mode),
    ))


class ExternalSideEffectChaosCertification:
    VERSION = "c8.3"

    def run(self) -> dict[str, Any]:
        scenarios = [
            self.timeout_after_commit(),
            self.crash_after_commit(),
            self.reject_before_commit(),
            self.duplicate_delivery(),
            self.reconciliation_before_retry(),
        ]
        passed = sum(s.passed for s in scenarios)
        return {
            "version": self.VERSION,
            "status": "PASS" if passed == len(scenarios) else "NOT_PROVEN",
            "passed": passed,
            "failed": len(scenarios) - passed,
            "scenarios": [s.__dict__ for s in scenarios],
            "production_provider_tested": False,
            "certification_boundary": "deterministic provider sandbox only",
        }

    def timeout_after_commit(self) -> ChaosScenario:
        p = SandboxProvider()
        key = "c83-timeout"
        first = _execute(p, key, "timeout_after_commit")
        remote = p.reconcile(idempotency_key=key)
        replay = _execute(p, key, "success")
        passed = (
            first["status"] == "uncertain"
            and remote["status"] == "committed"
            and replay["status"] == "succeeded"
            and replay["provider_object_id"] == remote["provider_object_id"]
            and p.committed_count == 1
            and p.call_count == 2
        )
        return ChaosScenario(
            "timeout_after_remote_commit", passed,
            "lost transport response never becomes a blind duplicate; provider identity is reconciled",
            {"first": first, "remote": remote, "replay": replay, "provider_calls": p.call_count, "committed": p.committed_count},
        )

    def crash_after_commit(self) -> ChaosScenario:
        p = SandboxProvider()
        key = "c83-crash"
        first = _execute(p, key, "crash_after_commit")
        remote = p.reconcile(idempotency_key=key)
        recovered = _execute(p, key, "success")
        passed = (
            first["status"] == "uncertain"
            and remote["status"] == "committed"
            and recovered["status"] == "succeeded"
            and recovered["provider_object_id"] == remote["provider_object_id"]
            and p.committed_count == 1
            and p.call_count == 2
        )
        return ChaosScenario(
            "worker_crash_after_remote_commit", passed,
            "worker loss after remote mutation is recoverable without a second remote object",
            {"first": first, "remote": remote, "recovered": recovered, "provider_calls": p.call_count, "committed": p.committed_count},
        )

    def reject_before_commit(self) -> ChaosScenario:
        p = SandboxProvider()
        key = "c83-reject"
        first = _execute(p, key, "reject_before_commit")
        retry = _execute(p, key + ":retry:1", "success")
        passed = first["status"] == "failed" and retry["status"] == "succeeded" and p.committed_count == 1
        return ChaosScenario(
            "provider_rejects_before_commit", passed,
            "authoritative pre-commit rejection permits a new governed retry identity",
            {"first": first, "retry": retry, "committed": p.committed_count},
        )

    def duplicate_delivery(self) -> ChaosScenario:
        p = SandboxProvider()
        key = "c83-duplicate"

        def call() -> dict[str, Any]:
            return _execute(p, key, "success")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: call(), range(2)))
        passed = p.committed_count == 1 and p.call_count == 2 and all(r["status"] == "succeeded" for r in results)
        return ChaosScenario(
            "concurrent_duplicate_delivery", passed,
            "at-least-once delivery cannot create two provider objects when provider idempotency is atomic",
            {"results": results, "provider_calls": p.call_count, "committed": p.committed_count},
        )

    def reconciliation_before_retry(self) -> ChaosScenario:
        p = SandboxProvider()
        key = "c83-reconcile-first"
        first = _execute(p, key, "timeout_after_commit")
        # A timeout is not retryable. Reconciliation is the authority that discovers
        # the committed remote object; the local executor remains fenced/uncertain.
        remote = p.reconcile(idempotency_key=key, correlation_id="corr-c83")
        local_retry = _execute(p, key, "success")
        passed = (
            first["status"] == "uncertain"
            and remote["status"] == "committed"
            and local_retry["status"] == "succeeded"
            and local_retry["provider_object_id"] == remote["provider_object_id"]
            and p.committed_count == 1
        )
        return ChaosScenario(
            "reconcile_before_unknown_retry", passed,
            "unknown external state requires positive reconciliation evidence before business success",
            {"first": first, "remote": remote, "post_reconciliation": local_retry, "committed": p.committed_count},
        )


def run_c83_certification() -> dict[str, Any]:
    return ExternalSideEffectChaosCertification().run()
