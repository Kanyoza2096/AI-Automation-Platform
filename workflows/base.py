from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
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


@dataclass
class StepResult:
    step_name: str
    status: StepStatus
    output: Any = None
    error: Optional[str] = None
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
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            logger.error("Step '%s' failed: %s", self.name, exc, exc_info=True)
            return StepResult(
                step_name=self.name,
                status=StepStatus.FAILED,
                error=str(exc),
                latency_ms=latency,
            )

    async def execute_async(self, context: dict[str, Any]) -> StepResult:
        start = time.monotonic()
        try:
            output = await self.async_run(context)
            latency = (time.monotonic() - start) * 1000
            logger.debug("Step '%s' succeeded in %.0fms", self.name, latency)
            return StepResult(step_name=self.name, status=StepStatus.SUCCESS, output=output, latency_ms=latency)
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            logger.error("Step '%s' failed: %s", self.name, exc, exc_info=True)
            return StepResult(step_name=self.name, status=StepStatus.FAILED, error=str(exc), latency_ms=latency)


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


    async def execute_async(self, initial_context: Optional[dict[str, Any]] = None, **kwargs) -> WorkflowResult:
        """Async-native workflow execution for ASGI and worker runtimes.

        Sync steps keep their existing implementation; steps that implement
        ``async_run`` can perform real async I/O without nested event loops.
        """
        context: dict[str, Any] = dict(initial_context or {})
        context.update(kwargs)
        result = WorkflowResult(workflow_name=self.name)
        logger.info("Workflow '%s' async started (%d steps)", self.name, len(self._steps))
        _emit("workflow_started", {"workflow": self.name, "total_steps": len(self._steps), "steps": [s.name for s in self._steps]})

        for step in self._steps:
            _emit("workflow_step_started", {"workflow": self.name, "step": step.name})
            step_result = await step.execute_async(context)
            result.steps.append(step_result)
            _emit("workflow_step_done", {
                "workflow": self.name, "step": step.name,
                "status": step_result.status.value, "latency_ms": round(step_result.latency_ms),
                "error": step_result.error,
            })
            if step_result.ok:
                context["last_output"] = step_result.output
            elif not step.continue_on_failure:
                logger.warning("Workflow '%s' halted at step '%s'", self.name, step.name)
                break

        result.finished_at = time.monotonic()
        logger.info("Workflow '%s' async finished in %.0fms — %s", self.name, result.total_ms, "OK" if result.success else "FAILED")
        _emit("workflow_done", {
            "workflow": self.name, "success": result.success, "total_ms": round(result.total_ms),
            "steps": [{"step": s.step_name, "status": s.status.value, "latency_ms": round(s.latency_ms)} for s in result.steps],
        })
        return result
