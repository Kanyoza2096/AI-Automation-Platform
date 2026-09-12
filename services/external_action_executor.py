"""Universal governed execution boundary for external side effects.

Provides a single intent -> execute -> receipt contract. It deliberately
supports at-least-once delivery with durable idempotency; exactly-once external
side effects depend on provider idempotency/reconciliation support.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable, Optional

from services.execution_ledger import ExecutionLedger, ExecutionRecord
from organism.goals import Goal
from organism.learning import LearningEngine


class ExternalActionExecutor:
    def __init__(self, ledger: ExecutionLedger, *, learning: Optional[LearningEngine] = None):
        self.ledger = ledger
        self.learning = learning

    def _learn(self, record: ExecutionRecord, *, goal: Optional[Goal], result: Optional[dict[str, Any]] = None) -> None:
        if self.learning is None:
            return
        try:
            self.learning.learn_from_record(record, goal=goal, result=result)
        except Exception:
            # Learning is derived intelligence, never execution authority. A learning
            # outage must not rewrite or hide an authoritative execution receipt.
            import logging
            logging.getLogger("kanyoza.external_action_executor").exception(
                "execution feedback learning failed id=%s", record.execution_id
            )

    async def execute(
        self,
        *,
        workspace_id: str,
        action: str,
        idempotency_key: str,
        provider: Optional[str],
        operation: Callable[[], Any],
        metadata: Optional[dict[str, Any]] = None,
        reconcile_on_exception: bool = True,
        goal: Optional[Goal] = None,
    ) -> dict[str, Any]:
        record = self.ledger.begin(
            workspace_id=workspace_id,
            action=action,
            idempotency_key=idempotency_key,
            provider=provider,
            metadata=metadata,
        )
        if record.status in {"succeeded", "uncertain", "cancelled"}:
            return {
                "status": record.status,
                "execution_id": record.execution_id,
                "provider_object_id": record.provider_object_id,
                "replayed": True,
            }

        record = self.ledger.claim(record)
        if record.status in {"succeeded", "uncertain", "cancelled"}:
            return {
                "status": record.status,
                "execution_id": record.execution_id,
                "provider_object_id": record.provider_object_id,
                "replayed": True,
            }
        if record.locked_by and record.locked_by != self.ledger.worker_id:
            return {
                "status": "in_progress",
                "execution_id": record.execution_id,
                "provider_object_id": record.provider_object_id,
                "replayed": False,
            }

        try:
            # Connector implementations in this repository are predominantly
            # synchronous (requests-based). Never run those network calls on the
            # FastAPI event loop. Async operations remain native; sync callables
            # execute in a worker thread.
            if inspect.iscoroutinefunction(operation):
                result = await operation()
            else:
                result = await asyncio.to_thread(operation)
                if inspect.isawaitable(result):
                    result = await result
            partial = False
            if isinstance(result, dict):
                # A multi-destination operation can partially succeed. That is
                # materially different from an ordinary provider rejection: some
                # external state may already exist, so callers must reconcile
                # rather than blindly retrying.
                partial = str(result.get("delivery_status") or result.get("execution_status") or "").lower() == "partial"
                ok = bool(result.get("ok", True))
                object_id = result.get("post_id") or result.get("id") or result.get("provider_object_id")
                request_id = result.get("request_id") or result.get("provider_request_id")
                provider_error = result.get("error")
                explicit_outcome = str(result.get("outcome") or "").lower()
            else:
                ok = bool(getattr(result, "ok", True))
                object_id = getattr(result, "post_id", None) or getattr(result, "provider_object_id", None)
                request_id = getattr(result, "request_id", None) or getattr(result, "provider_request_id", None)
                provider_error = getattr(result, "error", None)
                explicit_outcome = str(getattr(result, "outcome", "") or "").lower()
            if explicit_outcome in {"uncertain", "ambiguous"}:
                status = "uncertain"
            elif explicit_outcome == "failed":
                status = "failed"
            else:
                status = "uncertain" if partial else ("succeeded" if ok else "failed")
            try:
                updated = self.ledger.complete(
                    record, status=status, provider_request_id=request_id,
                    provider_object_id=str(object_id) if object_id else None,
                    error=None if ok else provider_error,
                    evidence={"provider": provider, "result_type": type(result).__name__, "partial_delivery": partial},
                )
            except Exception as ledger_exc:
                # The provider may already have mutated external state. If the
                # durable receipt cannot be committed, reporting ordinary failure
                # would invite a duplicate retry. Surface UNCERTAIN instead.
                return {
                    "status": "uncertain",
                    "execution_id": record.execution_id,
                    "provider_object_id": str(object_id) if object_id else None,
                    "replayed": False,
                    "error": f"Execution receipt persistence failed: {ledger_exc}",
                    "reconciliation_required": True,
                }
            return {"status": status, "execution_id": updated.execution_id,
                    "provider_object_id": updated.provider_object_id, "replayed": False,
                    "result": result}
        except Exception as exc:
            status = "uncertain" if reconcile_on_exception else "failed"
            try:
                updated = self.ledger.complete(
                    record, status=status, error=str(exc)[:500],
                    evidence={"provider": provider, "reconcile_required": status == "uncertain"},
                )
                self._learn(updated, goal=goal)
                return {"status": status, "execution_id": updated.execution_id,
                        "replayed": False, "error": str(exc)[:500]}
            except Exception as ledger_exc:
                # We cannot safely establish the terminal state. Never downgrade
                # this to a normal failure because the provider may have accepted
                # the request before the transport/ledger failure.
                return {
                    "status": "uncertain",
                    "execution_id": record.execution_id,
                    "replayed": False,
                    "error": f"External action and receipt persistence are uncertain: {exc}; {ledger_exc}",
                    "reconciliation_required": True,
                }
