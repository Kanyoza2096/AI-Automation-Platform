"""Canonical recovery orchestration for ambiguous external executions.

Recovery is deliberately observation-only: it may reconcile an existing external
side effect and update the authoritative execution ledger, but it never retries
the provider operation itself. A later retry, when safe, must return through the
Action Kernel and therefore receive a fresh governed execution lease.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from services.execution_ledger import ExecutionLedger, ExecutionRecord


class ExecutionReconciler(Protocol):
    def reconcile(self, *, idempotency_key: str, execution_id: str, provider_object_id: str | None = None) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class RecoveryResult:
    execution_id: str
    previous_status: str
    status: str
    authoritative: bool
    side_effect_reexecuted: bool
    reconciliation_required: bool
    provider_object_id: str | None = None
    evidence: str = ""


class ExecutionRecoveryOrchestrator:
    """Move one uncertain execution toward authoritative truth without replaying it."""

    def __init__(self, ledger: ExecutionLedger):
        self.ledger = ledger

    def recover(self, record: ExecutionRecord, reconciler: ExecutionReconciler) -> RecoveryResult:
        previous = record.status
        operational = self.ledger.operational_state(record)
        if not operational.reconciliation_required:
            return RecoveryResult(
                record.execution_id, previous, record.status, True, False, False,
                record.provider_object_id, "Execution does not require reconciliation",
            )

        observed = dict(reconciler.reconcile(
            idempotency_key=record.idempotency_key,
            execution_id=record.execution_id,
            provider_object_id=record.provider_object_id,
        ))
        state = str(observed.get("status") or observed.get("state") or "unknown").lower()
        authoritative = bool(observed.get("authoritative", False))
        object_id = observed.get("provider_object_id") or observed.get("post_id") or record.provider_object_id
        object_id = str(object_id) if object_id else None
        evidence = str(observed.get("evidence") or observed.get("reason") or "")[:500]

        if authoritative and state in {"published", "succeeded", "exists", "confirmed_success"} and object_id:
            updated = self.ledger.complete(
                record,
                status="succeeded",
                provider_request_id=observed.get("provider_request_id") or record.provider_request_id,
                provider_object_id=object_id,
                error=None,
                evidence={**(record.evidence or {}), "reconciliation": observed},
            )
            return RecoveryResult(updated.execution_id, previous, updated.status, True, False, False, object_id, evidence)

        if authoritative and state in {"not_found", "absent", "confirmed_absent"}:
            updated = self.ledger.complete(
                record,
                status="failed",
                provider_request_id=record.provider_request_id,
                provider_object_id=record.provider_object_id,
                error="Authoritative reconciliation confirmed the external object is absent",
                evidence={**(record.evidence or {}), "reconciliation": observed},
            )
            return RecoveryResult(updated.execution_id, previous, updated.status, True, False, False, updated.provider_object_id, evidence)

        updated = self.ledger.complete(
            record,
            status="uncertain",
            provider_request_id=record.provider_request_id,
            provider_object_id=object_id,
            error=evidence or "Provider reconciliation was inconclusive",
            evidence={**(record.evidence or {}), "reconciliation": observed},
        )
        return RecoveryResult(updated.execution_id, previous, updated.status, False, False, True, object_id, evidence)
