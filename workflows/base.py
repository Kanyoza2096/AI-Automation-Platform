from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _emit(event: str, data: dict) -> None:
    """Best-effort SocketIO emit to the /dashboard namespace."""
    try:
        import asyncio
        from core.socket_server import emit_to_dashboard
        loop = asyncio.get_running_loop()
        asyncio.create_task(emit_to_dashboard(event, data))
    except RuntimeError:
        pass  # No running event loop — skip emit (background thread context)
    except Exception:
        pass


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepFailure(Exception):
    """Structured failure raised from a workflow step.

    Carries a stable `code`, a human-readable message, and a `detail` dict
    with pipeline-specific context (trace, decision_id, connector status,
    reconciliation flags). The workflow runner extracts these fields into
    StepResult.error_code / error_detail so the workflow result envelope
    (and the API response) can carry them.

    Steps raise this instead of a bare Exception when they have structured
    failure information. Bare exceptions still work — they are wrapped into
    a StepResult with code="unexpected_exception" and no detail.
    """
    def __init__(
        self,
        message: str,
        *,
        code: str = "step_failed",
        detail: Optional[dict] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail: dict = detail or {}

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class StepResult:
    step_name: str
    status: StepStatus
    output: Any = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    error_detail: dict = field(default_factory=dict)
    latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == StepStatus.SUCCESS


@dataclass
class WorkflowResult:
    workflow_name: str
    steps: list[StepResult] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    finished_at: Optional[float] = None

    @property
    def success(self) -> bool:
        return all(s.ok or s.status == StepStatus.SKIPPED for s in self.steps)

    @property
    def total_ms(self) -> float:
        if self.finished_at:
            return (self.finished_at - self.started_at) * 1000
        return 0.0

    @property
    def last_output(self) -> Any:
        for s in reversed(self.steps):
            if s.ok and s.output is not None:
                return s.output
        return None

    @property
    def failed_step(self) -> Optional[StepResult]:
        """Return the first failed StepResult, or None if all succeeded."""
        for s in self.steps:
            if s.status == StepStatus.FAILED:
                return s
        return None

    def failure_evidence(self) -> dict[str, Any]:
        """Return a compact, JSON-safe diagnostic envelope for failed runs."""
        safe_detail_keys = {
            "exception_type", "context", "topic", "validation_status",
            "semantic_review_status", "semantic_review_reason",
            "semantic_review_request_id", "errors", "brand_id", "brand_name",
            "platform", "provider", "model", "decision_id", "raw_status",
            "reconciliation_required", "media_error_code", "reason", "status",
            "review_diagnostics", "decision", "rationale", "claims",
            "unsupported_claims", "evidence_coverage", "scores", "request_id",
            "approved", "support_status", "source_ids", "claim",
            "provider", "model", "assessed", "supported", "unsupported",
            "uncertain", "supported_rate", "claim_coverage", "brand_relevance",
            "originality", "audience_value", "genericity",
        }

        def safe_detail(detail: Any) -> dict[str, Any]:
            if not isinstance(detail, dict):
                return {}
            safe = {}
            for key in safe_detail_keys:
                if key not in detail:
                    continue
                value = detail[key]
                if isinstance(value, dict):
                    nested = safe_detail(value)
                    if nested:
                        safe[key] = nested
                elif isinstance(value, list):
                    if key in {"errors"}:
                        safe[key] = [str(item)[:500] for item in value[:20]]
                    elif key in {"claims", "unsupported_claims"}:
                        safe[key] = [
                            safe_detail(item) for item in value[:100]
                            if isinstance(item, dict)
                        ]
                    elif key == "source_ids":
                        safe[key] = [str(item)[:200] for item in value[:20]]
                elif isinstance(value, str):
                    safe[key] = value[:1000]
                elif isinstance(value, (int, float, bool)) or value is None:
                    safe[key] = value
            return safe

        failed = [
            {
                "step": step.step_name,
                "error": (step.error or "")[:2000],
                "error_code": step.error_code or "unknown",
                "detail": safe_detail(step.error_detail),
                "latency_ms": round(step.latency_ms),
            }
            for step in self.steps
            if step.status == StepStatus.FAILED
        ]
        return {
            "schema_version": 1,
            "workflow_name": self.workflow_name,
            "duration_ms": round(self.total_ms),
            "failed_steps": failed,
            "completed_steps": [
                step.step_name
                for step in self.steps
                if step.status == StepStatus.SUCCESS
            ],
        }


class Step:
    """A single stage in a Workflow. Override *run* to implement the logic."""

    name: str = "unnamed_step"
    continue_on_failure: bool = False

    def run(self, context: dict[str, Any]) -> Any:
        """Execute the step. Raise to signal failure. Return value is passed
        as *context['last_output']* to the next step."""
        raise NotImplementedError

    async def async_run(self, context: dict[str, Any]) -> Any:
        """Async execution hook. Sync steps retain their existing contract."""
        result = self.run(context)
        if inspect.isawaitable(result):
            return await result
        return result

    def execute(self, context: dict[str, Any]) -> StepResult:
        start = time.monotonic()
        try:
            output = self.run(context)
            latency = (time.monotonic() - start) * 1000
            logger.debug("Step '%s' succeeded in %.0fms", self.name, latency)
            return StepResult(
                step_name=self.name,
                status=StepStatus.SUCCESS,
                output=output,
                latency_ms=latency,
            )
        except StepFailure as exc:
            latency = (time.monotonic() - start) * 1000
            logger.error(
                "Step '%s' failed: code=%s msg=%s detail_keys=%s",
                self.name, exc.code, exc.message, sorted(exc.detail.keys()),
                exc_info=True,
            )
            return StepResult(
                step_name=self.name,
                status=StepStatus.FAILED,
                error=exc.message,
                error_code=exc.code,
                error_detail=exc.detail,
                latency_ms=latency,
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            logger.error("Step '%s' failed: %s", self.name, exc, exc_info=True)

            # Preserve governed application-error identity and a deliberately
            # small diagnostic context. Previously AIValidationError and other
            # KanyozaError subclasses were flattened to unexpected_exception,
            # losing the reason that a workflow gate rejected an operation.
            raw_code = getattr(exc, "code", None)
            error_code = getattr(raw_code, "value", raw_code) or "unexpected_exception"
            raw_context = getattr(exc, "context", None)
            safe_keys = {
                "topic", "validation_status", "semantic_review_status",
                "semantic_review_reason", "semantic_review_request_id",
                "errors", "brand_id", "platform", "provider", "model",
            }
            safe_context = {}
            if isinstance(raw_context, dict):
                for key in safe_keys:
                    value = raw_context.get(key)
                    if isinstance(value, str):
                        safe_context[key] = value[:1000]
                    elif isinstance(value, (int, float, bool)) or value is None:
                        if key in raw_context:
                            safe_context[key] = value
                    elif key == "errors" and isinstance(value, list):
                        safe_context[key] = [
                            str(item)[:500] for item in value[:20]
                        ]

            detail = {"exception_type": type(exc).__name__}
            if safe_context:
                detail["context"] = safe_context
            return StepResult(
                step_name=self.name,
                status=StepStatus.FAILED,
                error=str(getattr(exc, "message", None) or exc)[:2000],
                error_code=str(error_code),
                error_detail=detail,
                latency_ms=latency,
            )

    async def execute_async(self, context: dict[str, Any]) -> StepResult:
        start = time.monotonic()
        try:
            output = await self.async_run(context)
            latency = (time.monotonic() - start) * 1000
            logger.debug("Step '%s' succeeded in %.0fms", self.name, latency)
            return StepResult(
                step_name=self.name,
                status=StepStatus.SUCCESS,
                output=output,
                latency_ms=latency,
            )
        except StepFailure as exc:
            latency = (time.monotonic() - start) * 1000
            logger.error(
                "Step '%s' failed: code=%s msg=%s detail_keys=%s",
                self.name, exc.code, exc.message, sorted(exc.detail.keys()),
                exc_info=True,
            )
            return StepResult(
                step_name=self.name,
                status=StepStatus.FAILED,
                error=exc.message,
                error_code=exc.code,
                error_detail=exc.detail,
                latency_ms=latency,
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            logger.error("Step '%s' failed: %s", self.name, exc, exc_info=True)

            raw_code = getattr(exc, "code", None)
            error_code = getattr(raw_code, "value", raw_code) or "unexpected_exception"
            raw_context = getattr(exc, "context", None)
            safe_keys = {
                "topic", "validation_status", "semantic_review_status",
                "semantic_review_reason", "semantic_review_request_id",
                "errors", "brand_id", "platform", "provider", "model",
            }
            safe_context = {}
            if isinstance(raw_context, dict):
                for key in safe_keys:
                    value = raw_context.get(key)
                    if isinstance(value, str):
                        safe_context[key] = value[:1000]
                    elif isinstance(value, (int, float, bool)) or value is None:
                        if key in raw_context:
                            safe_context[key] = value
                    elif key == "errors" and isinstance(value, list):
                        safe_context[key] = [
                            str(item)[:500] for item in value[:20]
                        ]

            detail = {"exception_type": type(exc).__name__}
            if safe_context:
                detail["context"] = safe_context
            return StepResult(
                step_name=self.name,
                status=StepStatus.FAILED,
                error=str(getattr(exc, "message", None) or exc)[:2000],
                error_code=str(error_code),
                error_detail=detail,
                latency_ms=latency,
            )


class Workflow:
    """Ordered sequence of Steps that share a mutable context dict.

    Usage::

        result = PostPublishWorkflow(deps...).run(topic="FastAPI vs Flask")
    """

    name: str = "unnamed_workflow"

    def __init__(self, steps: list[Step]) -> None:
        self._steps = steps

    def run(self, **initial_context) -> WorkflowResult:
        result = WorkflowResult(workflow_name=self.name)
        context: dict[str, Any] = dict(initial_context)

        logger.info("Workflow '%s' started (%d steps)", self.name, len(self._steps))
        _emit("workflow_started", {
            "workflow": self.name,
            "total_steps": len(self._steps),
            "steps": [s.name for s in self._steps],
        })

        for step in self._steps:
            _emit("workflow_step_started", {
                "workflow": self.name,
                "step": step.name,
            })

            step_result = step.execute(context)
            result.steps.append(step_result)

            _emit("workflow_step_done", {
                "workflow": self.name,
                "step": step.name,
                "status": step_result.status.value,
                "latency_ms": round(step_result.latency_ms),
                "error": step_result.error,
                "error_code": step_result.error_code,
                "error_detail": step_result.error_detail,
            })

            if step_result.ok:
                context["last_output"] = step_result.output
            elif not step.continue_on_failure:
                logger.warning(
                    "Workflow '%s' halted at step '%s'", self.name, step.name
                )
                break

        result.finished_at = time.monotonic()
        ok = result.success
        logger.info(
            "Workflow '%s' finished in %.0fms — %s",
            self.name,
            result.total_ms,
            "OK" if ok else "FAILED",
        )
        _emit("workflow_done", {
            "workflow": self.name,
            "success": ok,
            "total_ms": round(result.total_ms),
            "steps": [
                {"step": s.step_name, "status": s.status.value, "latency_ms": round(s.latency_ms)}
                for s in result.steps
            ],
        })
        return result

    async def execute_async(
        self,
        initial_context: Optional[dict[str, Any]] = None,
        *,
        workflow_history: Optional[Any] = None,
        **kwargs,
    ) -> WorkflowResult:
        """Async-native workflow execution for ASGI and worker runtimes.

        Sync steps keep their existing implementation; steps that implement
        ``async_run`` can perform real async I/O without nested event loops.

        v-next: `workflow_history` — when provided, the completed
        WorkflowResult is appended to the history store so the
        /workflow/history endpoint (and the Workflow Runs page) can
        observe it. Previously the base class never wrote to the store,
        so the endpoint always returned an empty list even when workflows
        were executing. This is the missing write half of the reader that
        already existed.
        """
        context: dict[str, Any] = dict(initial_context or {})
        context.update(kwargs)
        result = WorkflowResult(workflow_name=self.name)
        logger.info("Workflow '%s' async started (%d steps)", self.name, len(self._steps))
        _emit("workflow_started", {
            "workflow": self.name,
            "total_steps": len(self._steps),
            "steps": [s.name for s in self._steps],
        })

        try:
            for step in self._steps:
                _emit("workflow_step_started", {"workflow": self.name, "step": step.name})
                step_result = await step.execute_async(context)
                result.steps.append(step_result)
                _emit("workflow_step_done", {
                    "workflow": self.name,
                    "step": step.name,
                    "status": step_result.status.value,
                    "latency_ms": round(step_result.latency_ms),
                    "error": step_result.error,
                    "error_code": step_result.error_code,
                    "error_detail": step_result.error_detail,
                })
                if step_result.ok:
                    context["last_output"] = step_result.output
                elif not step.continue_on_failure:
                    logger.warning("Workflow '%s' halted at step '%s'", self.name, step.name)
                    break
        finally:
            result.finished_at = time.monotonic()
            logger.info(
                "Workflow '%s' async finished in %.0fms — %s",
                self.name,
                result.total_ms,
                "OK" if result.success else "FAILED",
            )
            _emit("workflow_done", {
                "workflow": self.name,
                "success": result.success,
                "total_ms": round(result.total_ms),
                "steps": [
                    {"step": s.step_name, "status": s.status.value, "latency_ms": round(s.latency_ms)}
                    for s in result.steps
                ],
            })
            
            # ── v-next: persist the workflow result so /workflow/history can
            #    observe it. Only attempted when the caller provided a store;
            #    without it the workflow is a no-op history-wise (previous
            #    behavior preserved).
            if workflow_history is not None:
                try:
                    # Extract workspace_id if the context has a brand object
                    brand_obj = context.get("brand")
                    ws_id = context.get("workspace_id")
                    if not ws_id and brand_obj is not None:
                        ws_id = getattr(brand_obj, "workspace_id", None)

                    entry = {
                        "workflow": self.name,
                        "status": "SUCCEEDED" if result.success else "FAILED",
                        "success": result.success,
                        "total_ms": round(result.total_ms),
                        "started_at": datetime.fromtimestamp(
                            result.started_at, tz=timezone.utc
                        ).isoformat(),
                        "finished_at": datetime.fromtimestamp(
                            result.finished_at, tz=timezone.utc
                        ).isoformat() if result.finished_at else None,
                        "steps": [
                            {
                                "name": s.step_name,
                                "status": s.status.value,
                                "latency_ms": round(s.latency_ms),
                                "error": s.error,
                                "error_code": s.error_code,
                            }
                            for s in result.steps
                        ],
                        "workspace_id": ws_id,
                        "job_id": context.get("job_id"),
                    }
                    
                    # AsyncWorkflowHistory.append is async
                    maybe = workflow_history.append(entry)
                    if inspect.isawaitable(maybe):
                        await maybe
                except Exception as exc:
                    logger.warning(
                        "Workflow '%s': failed to persist to workflow history: %s",
                        self.name, exc,
                    )

        return result
