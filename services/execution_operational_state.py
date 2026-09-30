"""Canonical operational projection for durable executions.

ExecutionLedger remains the source of truth.  This module deliberately contains
no second execution state machine: it translates a ledger record into the
provider-neutral operational facts consumed by reconciliation, incidents and
Mission Control telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


TERMINAL_STATUSES = frozenset({"succeeded", "failed", "uncertain", "cancelled"})


@dataclass(frozen=True)
class ExecutionOperationalState:
    execution_id: str
    workspace_id: str
    action: str
    status: str
    attempt: int
    provider: Optional[str]
    provider_object_id: Optional[str]
    idempotency_key: str
    reconciliation_required: bool
    incident_required: bool
    health: str
    reason: Optional[str] = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def telemetry(self) -> dict[str, Any]:
        """Mission Control-safe projection; never invents provider state."""
        return {
            "execution_id": self.execution_id,
            "workspace_id": self.workspace_id,
            "action": self.action,
            "status": self.status,
            "attempt": self.attempt,
            "provider": self.provider,
            "provider_object_id": self.provider_object_id,
            "idempotency_key": self.idempotency_key,
            "reconciliation_required": self.reconciliation_required,
            "incident_required": self.incident_required,
            "health": self.health,
            "reason": self.reason,
        }

    def reconciliation_request(self) -> Optional[dict[str, Any]]:
        if not self.reconciliation_required:
            return None
        return {
            "execution_id": self.execution_id,
            "workspace_id": self.workspace_id,
            "action": self.action,
            "provider": self.provider,
            "provider_object_id": self.provider_object_id,
            "idempotency_key": self.idempotency_key,
            "reason": self.reason or "Execution outcome requires authoritative reconciliation",
        }

    def incident_signal(self) -> Optional[dict[str, Any]]:
        if not self.incident_required:
            return None
        return {
            "source": "execution_ledger",
            "component": "external_execution",
            "severity": "high" if self.status == "uncertain" else "medium",
            "symptom": f"external_execution_{self.status}",
            "correlation_id": self.execution_id,
            "workspace_id": self.workspace_id,
            "action": self.action,
            "provider": self.provider,
            "evidence": {
                "execution_id": self.execution_id,
                "provider_object_id": self.provider_object_id,
                "idempotency_key": self.idempotency_key,
                "reason": self.reason,
            },
        }


def project_execution(record: Any) -> ExecutionOperationalState:
    """Project one authoritative ledger record into operational state."""
    status = str(getattr(record, "status", "unknown") or "unknown").lower()
    object_id = getattr(record, "provider_object_id", None)

    if status == "succeeded":
        # A successful external operation without a provider receipt is not
        # treated as healthy success. The provider may have performed the side
        # effect while KDO failed to observe its identity.
        if not object_id:
            return ExecutionOperationalState(
                execution_id=record.execution_id,
                workspace_id=record.workspace_id,
                action=record.action,
                status="uncertain",
                attempt=int(record.attempt or 0),
                provider=record.provider,
                provider_object_id=None,
                idempotency_key=record.idempotency_key,
                reconciliation_required=True,
                incident_required=True,
                health="degraded",
                reason="Provider success has no canonical object id; reconciliation required",
            )
        return ExecutionOperationalState(
            execution_id=record.execution_id,
            workspace_id=record.workspace_id,
            action=record.action,
            status="succeeded",
            attempt=int(record.attempt or 0),
            provider=record.provider,
            provider_object_id=object_id,
            idempotency_key=record.idempotency_key,
            reconciliation_required=False,
            incident_required=False,
            health="healthy",
        )

    if status == "uncertain":
        return ExecutionOperationalState(
            execution_id=record.execution_id,
            workspace_id=record.workspace_id,
            action=record.action,
            status="uncertain",
            attempt=int(record.attempt or 0),
            provider=record.provider,
            provider_object_id=object_id,
            idempotency_key=record.idempotency_key,
            reconciliation_required=True,
            incident_required=True,
            health="degraded",
            reason=getattr(record, "error", None) or "External outcome is ambiguous",
        )

    if status == "failed":
        return ExecutionOperationalState(
            execution_id=record.execution_id,
            workspace_id=record.workspace_id,
            action=record.action,
            status="failed",
            attempt=int(record.attempt or 0),
            provider=record.provider,
            provider_object_id=object_id,
            idempotency_key=record.idempotency_key,
            reconciliation_required=False,
            incident_required=True,
            health="degraded",
            reason=getattr(record, "error", None) or "External execution failed",
        )

    if status == "cancelled":
        return ExecutionOperationalState(
            execution_id=record.execution_id,
            workspace_id=record.workspace_id,
            action=record.action,
            status="cancelled",
            attempt=int(record.attempt or 0),
            provider=record.provider,
            provider_object_id=object_id,
            idempotency_key=record.idempotency_key,
            reconciliation_required=False,
            incident_required=False,
            health="healthy",
            reason=getattr(record, "error", None),
        )

    return ExecutionOperationalState(
        execution_id=record.execution_id,
        workspace_id=record.workspace_id,
        action=record.action,
        status=status,
        attempt=int(record.attempt or 0),
        provider=record.provider,
        provider_object_id=object_id,
        idempotency_key=record.idempotency_key,
        reconciliation_required=False,
        incident_required=False,
        health="active",
    )
