"""Synchronous fail-closed Council + Action Kernel authorization for worker actions.

The quality pipeline has a synchronous policy callback. This adapter bridges that
callback to the async Council/Action Kernel workflow without accepting caller-
supplied decisions. It records a durable authorization action before the pipeline
performs its separately bounded operation. The receipt proves authorization only;
the operation's own result/evidence must still be collected independently.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import threading
import uuid
from contextlib import contextmanager
from collections.abc import Mapping
from typing import Any

from services.action_kernel import ActionContract
from services.kdo_engineering_governed_actions import (
    execute_council_governed_engineering_action,
)
from services.kdo_engineering_receipts import build_durable_action_receipt_verifier


ACTION_TYPE = "kdo_engineering_worker_action_authorize"
REQUIRED_PARAMETERS = (
    "tenant_id",
    "workspace_id",
    "task_id",
    "requested_action",
    "payload_digest",
    "authorization_request_id",
    "job_id",
    "plan_digest",
    "lease_token_digest",
)


class GovernedWorkerAuthorizationError(RuntimeError):
    """Raised when the real governance path cannot authorize a worker action."""


def _valid_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


class CouncilActionKernelWorkerAuthorizer:
    """Authorize each worker operation through live Council and durable Action Kernel.

    This adapter deliberately refuses an in-memory Action Kernel. The supplied
    kernel must use the same authoritative database that stores action_outbox and
    external_execution_ledger. The Council deliberation implementation is the
    configured KDO integration used by execute_council_governed_engineering_action.
    """

    def __init__(self, action_kernel: Any, *, event_store: Any = None) -> None:
        if action_kernel is None or not callable(getattr(action_kernel, "execute", None)):
            raise GovernedWorkerAuthorizationError("configured_action_kernel_required")
        db = getattr(action_kernel, "db", None)
        if db is None or not callable(getattr(db, "table", None)):
            raise GovernedWorkerAuthorizationError("durable_action_kernel_database_required")
        if getattr(action_kernel, "allow_in_memory", False):
            raise GovernedWorkerAuthorizationError("in_memory_action_kernel_forbidden")
        self._kernel = action_kernel
        self._event_store = event_store
        self._receipt_verifier = build_durable_action_receipt_verifier(db)
        self._lock = threading.RLock()
        self._execution_context = contextvars.ContextVar("kdo_worker_execution_context", default=None)
        self._register_contract()

    def _register_contract(self) -> None:
        metadata = self._kernel.get_contract_metadata(ACTION_TYPE)
        if metadata is None:
            def authorize_handler(parameters: Mapping[str, Any], _intent: Any) -> dict[str, Any]:
                # Revalidate the exact active lease inside Action Kernel execution,
                # after Council deliberation and immediately before recording approval.
                bound = self._execution_context.get()
                if not bound:
                    raise GovernedWorkerAuthorizationError("worker_execution_context_missing")
                context, lease_check = bound
                expected = {
                    "job_id": context.get("job_id"),
                    "plan_digest": context.get("plan_digest"),
                    "tenant_id": context.get("tenant_id"),
                    "workspace_id": context.get("workspace_id"),
                    "task_id": context.get("task_id"),
                    "lease_token_digest": hashlib.sha256(
                        str(context.get("lease_token", "")).encode("utf-8")
                    ).hexdigest(),
                }
                if any(str(parameters.get(key, "")) != str(value) for key, value in expected.items()):
                    raise GovernedWorkerAuthorizationError("worker_execution_scope_mismatch")
                if lease_check() is not True:
                    raise GovernedWorkerAuthorizationError("worker_lease_not_active")
                # This is an authorization-record action, not the filesystem or
                # test-runner operation itself. Its output is independently verified.
                request_id = parameters.get("authorization_request_id")
                return {
                    "status": "succeeded",
                    "provider_object_id": str(request_id),
                    "evidence": {
                        "requested_action": parameters.get("requested_action"),
                        "payload_digest": parameters.get("payload_digest"),
                        "authorization_only": True,
                    },
                }

            self._kernel.register(ActionContract(
                action_type=ACTION_TYPE,
                risk_level="high",
                required_parameters=REQUIRED_PARAMETERS,
                irreversible=False,
                requires_approval=True,
                inline_with_decision=True,
                handler=authorize_handler,
            ))
            metadata = self._kernel.get_contract_metadata(ACTION_TYPE)

        if not isinstance(metadata, Mapping) or not (
            metadata.get("action_type") == ACTION_TYPE
            and metadata.get("risk_level") == "high"
            and tuple(metadata.get("required_parameters") or ()) == REQUIRED_PARAMETERS
            and metadata.get("requires_approval") is True
            and metadata.get("inline_with_decision") is True
        ):
            raise GovernedWorkerAuthorizationError("worker_authorization_contract_mismatch")

    @contextmanager
    def bind_execution_context(self, context: Mapping[str, Any], lease_check: Any):
        """Bind every action to one currently leased queue job on this worker thread."""
        required = ("job_id", "plan_digest", "lease_token", "tenant_id", "workspace_id", "task_id")
        if not isinstance(context, Mapping) or any(not _valid_text(context.get(k)) for k in required):
            raise GovernedWorkerAuthorizationError("complete_worker_execution_context_required")
        if not callable(lease_check):
            raise GovernedWorkerAuthorizationError("active_lease_validator_required")
        token = self._execution_context.set((dict(context), lease_check))
        try:
            yield
        finally:
            self._execution_context.reset(token)

    def authorize(self, action: str, task: Mapping[str, Any]) -> bool:
        """Authorize only while the exact job/plan/lease remains active."""
        if not _valid_text(action) or not isinstance(task, Mapping):
            return False
        scope_keys = ("tenant_id", "workspace_id", "task_id")
        if any(not _valid_text(task.get(key)) for key in scope_keys):
            return False

        bound = self._execution_context.get()
        if not bound:
            return False
        context, lease_check = bound
        if any(str(task.get(key, "")) != str(context.get(key, "")) for key in scope_keys):
            return False
        try:
            if lease_check() is not True:
                return False
        except Exception:
            return False
        lease_token = str(context["lease_token"])
        lease_token_digest = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
        safe_task = {key: str(task[key]) for key in scope_keys}
        safe_task.update({
            "job_id": str(context["job_id"]),
            "plan_digest": str(context["plan_digest"]),
            "lease_token_digest": lease_token_digest,
        })
        criteria = task.get("acceptance_criteria", ())
        if isinstance(criteria, (list, tuple)):
            safe_task["acceptance_criteria"] = [
                str(value)[:500] for value in criteria if isinstance(value, str)
            ][:30]
        canonical = json.dumps(
            {"action": action, "task": safe_task},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        payload_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        request_id = str(uuid.uuid4())
        parameters = {
            **safe_task,
            "requested_action": action,
            "payload_digest": payload_digest,
            "authorization_request_id": request_id,
            "job_id": str(context["job_id"]),
            "plan_digest": str(context["plan_digest"]),
            "lease_token_digest": lease_token_digest,
        }
        objective = (
            f"Authorize engineering worker operation '{action}' for task "
            f"{safe_task['task_id']} in job {safe_task['job_id']} "
            f"under approved plan {safe_task['plan_digest']}; lease is revalidated."
        )

        # Worker execution invokes this synchronous adapter from its worker thread.
        # If an event loop is already running in this thread, fail closed rather
        # than nest loops or move authority-bearing work to an unmanaged thread.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return False

        def verify_bound_receipt(scope: Mapping[str, Any], receipt: Mapping[str, Any]) -> bool:
            # Read-back must prove the exact job, plan, lease digest and operation
            # payload, and the lease must still be active after Action Kernel returns.
            try:
                if lease_check() is not True:
                    return False
                bound_scope = {
                    **dict(scope),
                    "job_id": context["job_id"],
                    "plan_digest": context["plan_digest"],
                    "lease_token_digest": lease_token_digest,
                    "payload_digest": payload_digest,
                }
                return self._receipt_verifier.verify(bound_scope, receipt) is True
            except Exception:
                return False

        with self._lock:
            try:
                result = asyncio.run(execute_council_governed_engineering_action(
                    task=safe_task,
                    action_type=ACTION_TYPE,
                    parameters=parameters,
                    agent_id="kdo-engineering-worker",
                    tool_name="engineering-worker",
                    objective=objective,
                    action_kernel=self._kernel,
                    event_store=self._event_store,
                    receipt_verifier=verify_bound_receipt,
                    idempotency_key=(
                        f"kdo-worker-auth:{safe_task['tenant_id']}:"
                        f"{safe_task['workspace_id']}:{safe_task['task_id']}:"
                        f"{safe_task['job_id']}:{safe_task['plan_digest']}:{lease_token_digest}:{action}:{request_id}"
                    ),
                ))
            except Exception:
                return False
        return (
            isinstance(result, Mapping)
            and result.get("status") == "succeeded"
            and result.get("ok") is True
            and result.get("receipt_verified") is True
            and isinstance(result.get("receipt"), Mapping)
        )


def build_council_action_kernel_worker_authorizer(
    action_kernel: Any, *, event_store: Any = None,
) -> CouncilActionKernelWorkerAuthorizer:
    """Construct the production worker authorizer; fails if durable governance is absent."""
    return CouncilActionKernelWorkerAuthorizer(action_kernel, event_store=event_store)
