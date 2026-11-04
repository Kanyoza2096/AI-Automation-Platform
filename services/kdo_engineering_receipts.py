"""Authoritative read/verification for KDO engineering Action Kernel receipts.

This adapter reads both the durable Action Kernel outbox projection and the
external execution ledger. A receipt is accepted only when the two records
agree on scope, decision, action, intent, execution identity, and terminal
success. Missing rows, malformed responses, and database errors fail closed.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.execution_ledger import _workspace_uuid


class DurableReceiptVerificationError(RuntimeError):
    """Raised by strict verification helpers when authoritative evidence fails."""


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    if isinstance(data, Mapping):
        return [dict(data)]
    if isinstance(data, list):
        return [dict(row) for row in data if isinstance(row, Mapping)]
    return []


def _single_row(db: Any, table: str, filters: Mapping[str, Any]) -> dict[str, Any] | None:
    query = db.table(table).select("*")
    for key, value in filters.items():
        query = query.eq(key, value)
    response = query.execute()
    rows = _rows(response)
    # Ambiguous duplicates are not authority, even if one row looks valid.
    return rows[0] if len(rows) == 1 else None


class DurableActionReceiptVerifier:
    """Callable receipt verifier backed by the canonical durable database.

    The supplied database client must have server-side permissions appropriate
    for reading both tables. Do not expose a service-role client to end users.
    """

    def __init__(self, db: Any):
        if db is None or not callable(getattr(db, "table", None)):
            raise ValueError("authoritative_database_client_required")
        self._db = db

    def verify(self, scope: Mapping[str, Any], receipt: Mapping[str, Any]) -> bool:
        try:
            workspace_id = scope.get("workspace_id")
            tenant_id = scope.get("tenant_id")
            task_id = scope.get("task_id")
            decision_id = scope.get("decision_id")
            action_type = scope.get("action_type")
            execution_id = receipt.get("execution_id")
            intent_id = receipt.get("intent_id")
            if not all(isinstance(v, str) and v.strip() for v in (
                workspace_id, tenant_id, task_id, decision_id, action_type,
                execution_id, intent_id,
            )):
                return False

            intent = _single_row(self._db, "action_outbox", {
                "workspace_id": workspace_id,
                "intent_id": intent_id,
            })
            if intent is None:
                return False
            params = intent.get("parameters")
            if not isinstance(params, Mapping):
                return False
            if not (
                intent.get("status") == "succeeded"
                and intent.get("action_type") == action_type
                and intent.get("decision_id") == decision_id
                and intent.get("execution_id") == execution_id
                and str(params.get("tenant_id", "")) == tenant_id
                and str(params.get("workspace_id", "")) == workspace_id
                and str(params.get("task_id", "")) == task_id
            ):
                return False
            # When supplied by the engineering queue gate, also bind the
            # authoritative intent to the exact approved plan and queue key.
            if "plan_digest" in scope and params.get("plan_digest") != scope.get("plan_digest"):
                return False
            if ("job_idempotency_key" in scope
                    and params.get("job_idempotency_key") != scope.get("job_idempotency_key")):
                return False

            execution = _single_row(self._db, "external_execution_ledger", {
                "workspace_id": _workspace_uuid(workspace_id),
                "execution_id": execution_id,
            })
            if execution is None:
                return False
            if not (
                execution.get("status") == "succeeded"
                and execution.get("action") == f"action.{action_type}"
                and isinstance(execution.get("provider_object_id"), str)
                and bool(execution.get("provider_object_id", "").strip())
            ):
                return False

            evidence = execution.get("evidence")
            if not isinstance(evidence, Mapping):
                return False
            return (
                evidence.get("intent_id") == intent_id
                and evidence.get("decision_id") == decision_id
                and evidence.get("source") == "kdo_engineering_vertical"
            )
        except Exception:
            # Database errors are unknown evidence, never implicit approval.
            return False

    def __call__(self, scope: Mapping[str, Any], receipt: Mapping[str, Any]) -> bool:
        return self.verify(scope, receipt)


def build_durable_action_receipt_verifier(db: Any) -> DurableActionReceiptVerifier:
    """Construct the verifier for the same canonical database as ActionKernel."""
    return DurableActionReceiptVerifier(db)
