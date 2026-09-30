"""Universal governed execution boundary for external side effects.

Provides a single intent -> execute -> receipt contract. It deliberately
supports at-least-once delivery with durable idempotency; exactly-once external
side effects depend on provider idempotency/reconciliation support.

Receipt contract (v2):
  - `succeeded` requires a concrete provider object ID. A connector that
    reports `ok=True` without an ID is classified as `uncertain`, never
    `succeeded`.
  - Timeouts and transport errors are inherently ambiguous and are classified
    as `uncertain`, never `failed`, because the provider may have accepted
    the request before the failure.
  - Every completion receipt persists provider, object ID, and the raw result
    metadata so reconciliation can re-derive the truth later.

v2.1 (2026-09-29):
  - `execute()` return dict now carries a normalized `error` string. Providers
    (including PublishingResult.failure().to_dict()) can return structured
    error payloads like {"type": "...", "message": "...", "retryable": bool}.
    Downstream consumers (kernel, canonical pipeline, workflow step) call
    exec_result.get("error") expecting a string. When the executor only
    stored the structured error in the ledger and omitted it from the return,
    the pipeline fell back to a generic "External publish failed" string,
    hiding the real cause. `_normalize_error` flattens dicts/objects to a
    message-bearing string so the real reason propagates end-to-end.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable, Optional

from services.execution_ledger import ExecutionLedger, ExecutionRecord
from services.receipt_protocol import receipt_from_result
from organism.goals import Goal
from organism.learning import LearningEngine


# Provider object ID keys we accept as a valid receipt across connector
# implementations. Adding a key here is cheaper and safer than teaching every
# caller to inspect connector-specific shapes.
_PROVIDER_OBJECT_ID_KEYS = (
    "post_id",
    "platform_post_id",
    "id",
    "provider_object_id",
    "tweet_id",
    "message_id",
    "provider_message_id",
    "transaction_id",
    "object_id",
)


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

    async def _execution_heartbeat(self, record: ExecutionRecord, stop: asyncio.Event, *, interval: int) -> None:
        """Keep the durable Execution Ledger lease alive during slow providers."""
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                try:
                    self.ledger.renew(record, lease_seconds=max(30, interval * 3))
                except Exception:
                    import logging
                    logging.getLogger("kanyoza.external_action_executor").warning(
                        "execution lease renewal failed id=%s", record.execution_id, exc_info=True
                    )
                    # Do not cancel an in-flight provider operation. We cannot prove
                    # remote cancellation; completion fencing will force uncertainty
                    # if ownership is lost, preserving the reconciliation invariant.
                    return

    # ── Receipt extraction ────────────────────────────────────────────

    @staticmethod
    def _extract_result_fields(result: Any) -> dict:
        """Normalize a connector result (dict or dataclass) into a uniform view.

        Returns:
            ok: bool — connector claims success
            object_id: Optional[str] — provider-side identifier, if any
            request_id: Optional[str] — provider request ID, if any
            provider_error: Any — provider error payload, if any
            explicit_outcome: str — "succeeded" | "failed" | "uncertain" | ""
            partial: bool — multi-destination partial success signal
        """
        if isinstance(result, dict):
            ok = bool(result.get("ok", True))
            object_id = None
            for key in _PROVIDER_OBJECT_ID_KEYS:
                value = result.get(key)
                if value:
                    object_id = value
                    break
            request_id = result.get("request_id") or result.get("provider_request_id")
            provider_error = result.get("error")
            explicit_outcome = str(result.get("outcome") or "").lower()
            partial = str(
                result.get("delivery_status")
                or result.get("execution_status")
                or ""
            ).lower() == "partial"
            return {
                "ok": ok,
                "object_id": object_id,
                "request_id": request_id,
                "provider_error": provider_error,
                "explicit_outcome": explicit_outcome,
                "partial": partial,
            }

        # Dataclass / object path (ConnectorResult and friends)
        ok = bool(getattr(result, "ok", True))
        object_id = None
        for key in _PROVIDER_OBJECT_ID_KEYS:
            value = getattr(result, key, None)
            if value:
                object_id = value
                break
        request_id = (
            getattr(result, "request_id", None)
            or getattr(result, "provider_request_id", None)
        )
        provider_error = getattr(result, "error", None)
        explicit_outcome = str(getattr(result, "outcome", "") or "").lower()
        partial = str(
            getattr(result, "delivery_status", "")
            or getattr(result, "execution_status", "")
            or ""
        ).lower() == "partial"
        return {
            "ok": ok,
            "object_id": object_id,
            "request_id": request_id,
            "provider_error": provider_error,
            "explicit_outcome": explicit_outcome,
            "partial": partial,
        }

    @staticmethod
    def _normalize_error(provider_error: Any) -> Optional[str]:
        """Return a human-readable string for a provider error.

        Providers can return error payloads in several shapes:
          - None (no error reported)
          - a plain string ("No Facebook credentials for brand 'X'")
          - a structured dict from PublishingResult.failure().to_dict():
            {"type": "TOKEN_MISSING", "message": "...", "retryable": bool}
          - an arbitrary object with a `.message` / `.reason` attribute

        Downstream code (kernel, pipeline, workflow, logs) calls
        `result.get("error")` and expects a string it can log, persist, and
        return to callers. Normalizing here means the ledger row, the
        kernel's error string, and the pipeline response all carry the same
        human-readable cause instead of a truncated dict or a generic
        fallback like "External publish failed".
        """
        if provider_error is None:
            return None
        if isinstance(provider_error, str):
            return provider_error or None

        if isinstance(provider_error, dict):
            message = (
                provider_error.get("message")
                or provider_error.get("reason")
                or provider_error.get("error_message")
            )
            code = (
                provider_error.get("type")
                or provider_error.get("code")
                or provider_error.get("error_code")
            )
            if message and code:
                return f"{code}: {message}"
            if message:
                return str(message)
            if code:
                return str(code)
            # No recognizable shape — stringify so nothing is silently lost.
            try:
                return str(provider_error)
            except Exception:
                return "Provider returned an unprintable error payload"

        # Object with attributes (e.g., a ConnectorResult dataclass).
        message = getattr(provider_error, "message", None) or getattr(
            provider_error, "reason", None
        )
        code = getattr(provider_error, "code", None) or getattr(
            provider_error, "error_code", None
        )
        if message and code:
            return f"{code}: {message}"
        if message:
            return str(message)
        if code:
            return str(code)
        try:
            return str(provider_error)
        except Exception:
            return "Provider returned an unprintable error payload"

    @staticmethod
    def _classify_status(fields: dict) -> str:
        """Return the honest terminal status for a provider result.

        Rules:
          - explicit outcome wins when present
          - partial delivery is always uncertain
          - success without a provider object ID is NOT success
          - everything else with `ok=True` and an ID is succeeded
          - `ok=False` with a provider error is failed
        """
        explicit = fields["explicit_outcome"]
        if explicit in {"uncertain", "ambiguous"}:
            return "uncertain"
        if explicit == "failed":
            return "failed"
        if explicit == "succeeded":
            # Even an explicit "succeeded" must carry an object ID.
            return "succeeded" if fields["object_id"] else "uncertain"
        if fields["partial"]:
            return "uncertain"
        if fields["ok"]:
            # Receipt contract: ok without an ID is fabricated success.
            return "succeeded" if fields["object_id"] else "uncertain"
        return "failed"

    # ── Main entry point ──────────────────────────────────────────────

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
        execution_lease_seconds: int = 300,
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
                "receipt": {
                    "provider": record.provider,
                    "status": record.status,
                    "provider_object_id": record.provider_object_id,
                },
                "replayed": True,
            }

        record = self.ledger.claim(record)
        if record.status in {"succeeded", "uncertain", "cancelled"}:
            return {
                "status": record.status,
                "execution_id": record.execution_id,
                "provider_object_id": record.provider_object_id,
                "receipt": {
                    "provider": record.provider,
                    "status": record.status,
                    "provider_object_id": record.provider_object_id,
                },
                "replayed": True,
            }
        if record.locked_by and record.locked_by != self.ledger.worker_id:
            return {
                "status": "in_progress",
                "execution_id": record.execution_id,
                "provider_object_id": record.provider_object_id,
                "replayed": False,
            }

        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._execution_heartbeat(
                record, heartbeat_stop,
                interval=max(10, min(int(execution_lease_seconds), 3600) // 3),
            )
        )
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

            fields = self._extract_result_fields(result)
            receipt = receipt_from_result(result, provider=provider)
            status = receipt.status

            # Only persist a provider_object_id when the status is a real
            # success. An uncertain outcome must NOT retain the object ID as if
            # it were a receipt; the ID will be re-derived via reconciliation.
            persisted_object_id = (
                str(fields["object_id"])
                if (status == "succeeded" and fields["object_id"])
                else None
            )

            evidence = {
                "provider": provider,
                "result_type": type(result).__name__,
                "partial_delivery": fields["partial"],
                "raw_outcome": fields["explicit_outcome"] or None,
                "had_object_id": bool(fields["object_id"]),
                "classified_status": status,
            }

            # Compute the human-readable error once. The same string goes
            # into the ledger (durable truth) AND the return dict (so the
            # kernel, pipeline, and workflow step can name the real cause).
            error_string: Optional[str] = None
            if status != "succeeded":
                error_string = self._normalize_error(fields["provider_error"])
                if not error_string and status == "uncertain" and fields["ok"]:
                    error_string = "Provider returned success without an object ID"

            try:
                updated = self.ledger.complete(
                    record,
                    status=status,
                    provider_request_id=fields["request_id"],
                    provider_object_id=persisted_object_id,
                    error=error_string,
                    evidence=evidence,
                )
            except Exception as ledger_exc:
                # The provider may already have mutated external state. If the
                # durable receipt cannot be committed, reporting ordinary failure
                # would invite a duplicate retry. Surface UNCERTAIN instead.
                return {
                    "status": "uncertain",
                    "execution_id": record.execution_id,
                    "provider_object_id": (
                        str(fields["object_id"]) if fields["object_id"] else None
                    ),
                    "replayed": False,
                    "error": f"Execution receipt persistence failed: {ledger_exc}",
                    "receipt": receipt.to_dict(),
                    "reconciliation_required": True,
                }

            self._learn(updated, goal=goal, result=result if isinstance(result, dict) else None)

            return {
                "status": status,
                "execution_id": updated.execution_id,
                "provider_object_id": updated.provider_object_id,
                "replayed": False,
                "result": result,
                "receipt": receipt.to_dict(),
                # Real reason when not succeeded; None when succeeded.
                "error": error_string,
                # Explicit signal for the outbox: any non-succeeded outcome
                # requires reconciliation, not a blind retry.
                "reconciliation_required": status == "uncertain",
            }

        except Exception as exc:
            # Timeouts and transport errors are inherently ambiguous for
            # external side effects. The provider may have accepted the request
            # before the response was lost. Never downgrade to "failed" unless
            # the caller explicitly opts out of reconciliation.
            status = "uncertain" if reconcile_on_exception else "failed"
            exception_receipt = {
                "provider": provider,
                "status": status,
                "provider_object_id": None,
                "provider_request_id": None,
                "observed_object_id": None,
                "error": str(exc)[:500],
                "metadata": {"exception_type": type(exc).__name__},
            }
            try:
                updated = self.ledger.complete(
                    record,
                    status=status,
                    error=str(exc)[:500],
                    evidence={
                        "provider": provider,
                        "reconcile_required": status == "uncertain",
                        "exception_type": type(exc).__name__,
                    },
                )
                self._learn(updated, goal=goal)
                return {
                    "status": status,
                    "execution_id": updated.execution_id,
                    "replayed": False,
                    "error": str(exc)[:500],
                    "receipt": exception_receipt,
                    "reconciliation_required": status == "uncertain",
                }
            except Exception as ledger_exc:
                # We cannot safely establish the terminal state. Never downgrade
                # this to a normal failure because the provider may have accepted
                # the request before the transport/ledger failure.
                return {
                    "status": "uncertain",
                    "execution_id": record.execution_id,
                    "replayed": False,
                    "error": f"External action and receipt persistence are uncertain: {exc}; {ledger_exc}",
                    "receipt": {**exception_receipt, "status": "uncertain"},
                    "reconciliation_required": True,
                }
        finally:
            heartbeat_stop.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
