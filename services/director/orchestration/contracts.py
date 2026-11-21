"""Typed, bounded contracts for Director native-engine task adapters.

This module is deliberately independent of FastAPI, Supabase, and engine
implementations. It defines the safe invocation boundary; orchestration
state and persistence remain owned by Director.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class MissionTaskContext:
    task_id: str
    mission_id: str
    workspace_id: str | None
    capability: str
    objective: str
    inputs: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: Sequence[str] = field(default_factory=tuple)
    correlation_id: str = ""
    attempt_id: str = ""
    timeout_seconds: float | None = None
    scope_type: str = "workspace"

    def __post_init__(self) -> None:
        if self.scope_type not in {"workspace", "platform"}:
            raise ValueError("invalid_task_context_scope")
        if self.scope_type == "workspace" and not str(self.workspace_id or "").strip():
            raise ValueError("workspace_task_context_requires_workspace_id")
        if self.scope_type == "platform" and self.workspace_id is not None:
            raise ValueError("platform_task_context_cannot_have_workspace_id")
        for name in ("task_id", "mission_id", "capability", "objective"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name}_required")
            object.__setattr__(self, name, value)
        if not str(self.correlation_id or "").strip():
            raise ValueError("correlation_id_required")
        object.__setattr__(self, "correlation_id", str(self.correlation_id).strip())
        if self.attempt_id:
            object.__setattr__(self, "attempt_id", str(self.attempt_id).strip())
        if not isinstance(self.inputs, Mapping):
            raise TypeError("task_inputs_must_be_mapping")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds_must_be_positive")


@dataclass(frozen=True)
class EngineContribution:
    engine_id: str
    capability: str
    task_id: str
    mission_id: str
    workspace_id: str | None
    status: str
    summary: str
    findings: Sequence[str] = field(default_factory=tuple)
    recommendations: Sequence[str] = field(default_factory=tuple)
    evidence: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    limitations: Sequence[str] = field(default_factory=tuple)
    confidence: float | None = None
    source_metadata: Mapping[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    started_at: datetime = field(default_factory=_utcnow)
    finished_at: datetime = field(default_factory=_utcnow)
    authority: str = "none"
    side_effect_class: str = "read_only"
    scope_type: str = "workspace"

    def __post_init__(self) -> None:
        if self.scope_type not in {"workspace", "platform"}:
            raise ValueError("invalid_contribution_scope")
        if self.scope_type == "workspace" and not str(self.workspace_id or "").strip():
            raise ValueError("workspace_contribution_requires_workspace_id")
        if self.scope_type == "platform" and self.workspace_id is not None:
            raise ValueError("platform_contribution_cannot_have_workspace_id")
        if self.status not in {"completed", "partial", "unavailable", "failed"}:
            raise ValueError("invalid_contribution_status")
        if self.authority != "none":
            raise ValueError("engine_contribution_cannot_grant_authority")
        if self.side_effect_class not in {"read_only", "external_read_only", "internal_record"}:
            raise ValueError("native_adapter_side_effect_class_not_advisory_safe")
        if self.confidence is not None and not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence_out_of_range")


@dataclass(frozen=True)
class ExecutionAttemptReference:
    attempt_id: str
    engine_id: str
    capability: str
    task_id: str
    mission_id: str
    workspace_id: str | None
    correlation_id: str
    scope_type: str = "workspace"

    def __post_init__(self) -> None:
        for name in (
            "attempt_id", "engine_id", "capability", "task_id",
            "mission_id", "correlation_id",
        ):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"execution_attempt_{name}_required")
            object.__setattr__(self, name, value)
        if self.scope_type not in {"workspace", "platform"}:
            raise ValueError("invalid_execution_attempt_scope")
        if self.scope_type == "workspace" and not str(self.workspace_id or "").strip():
            raise ValueError("workspace_execution_attempt_requires_workspace_id")
        if self.scope_type == "platform" and self.workspace_id is not None:
            raise ValueError("platform_execution_attempt_cannot_have_workspace_id")


@dataclass(frozen=True)
class EngineAttemptReconciliation:
    attempt_id: str
    engine_id: str
    status: str
    authoritative: bool
    evidence_ref: str | None = None
    result: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.attempt_id or "").strip() or not str(self.engine_id or "").strip():
            raise ValueError("engine_attempt_reconciliation_identity_required")
        if self.status not in {"not_started", "running", "completed", "failed_no_side_effects", "unknown"}:
            raise ValueError("invalid_engine_attempt_reconciliation_status")
        if not isinstance(self.authoritative, bool):
            raise ValueError("engine_attempt_authoritative_flag_required")
        if self.status != "unknown" and (
            not self.authoritative
            or not str(self.evidence_ref or "").strip()
            or len(str(self.evidence_ref)) > 2048
        ):
            raise ValueError("authoritative_reconciliation_evidence_required")
        if not isinstance(self.result, Mapping):
            raise TypeError("engine_attempt_result_must_be_mapping")
        if self.status == "completed" and (
            not str(self.result.get("summary") or "").strip()
            or len(str(self.result.get("summary") or "")) > 5000
            or not str(self.result.get("completed_at") or "").strip()
        ):
            raise ValueError("completed_attempt_result_summary_and_timestamp_required")


class ReconciliableNativeEngineAdapter(Protocol):
    """Optional adapter extension; absence means the outcome stays unknown."""

    async def reconcile_attempt(
        self, reference: ExecutionAttemptReference
    ) -> EngineAttemptReconciliation:
        """Return authoritative status for this exact attempt; never invoke or replay it."""


class NativeEngineAdapter(Protocol):
    engine_id: str
    capabilities: frozenset[str]
    max_concurrency: int
    timeout_seconds: float

    async def invoke(self, context: MissionTaskContext) -> EngineContribution:
        """Invoke the engine's native runtime; never dispatch external actions."""


class AdapterInvocationTimeout(TimeoutError):
    pass


class AdapterRegistry:
    """Explicit registry with bounded per-engine concurrency and timeouts.

    Registration is explicit: observer discovery or an LLM persona label is
    never treated as proof that an engine can execute a native task.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, NativeEngineAdapter] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def register(self, adapter: NativeEngineAdapter) -> None:
        engine_id = str(getattr(adapter, "engine_id", "") or "").strip()
        capabilities = frozenset(getattr(adapter, "capabilities", ()) or ())
        max_concurrency = getattr(adapter, "max_concurrency", 0)
        timeout_seconds = getattr(adapter, "timeout_seconds", 0)

        if not engine_id or not capabilities:
            raise ValueError("adapter_identity_and_capabilities_required")
        if engine_id in self._adapters:
            raise ValueError(f"adapter_already_registered:{engine_id}")
        if not isinstance(max_concurrency, int) or max_concurrency < 1:
            raise ValueError("adapter_max_concurrency_must_be_positive")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("adapter_timeout_must_be_positive")
        if any(not isinstance(item, str) or not item.strip() for item in capabilities):
            raise ValueError("adapter_capabilities_must_be_nonempty_strings")

        self._adapters[engine_id] = adapter
        self._semaphores[engine_id] = asyncio.Semaphore(max_concurrency)

    def get(self, engine_id: str) -> NativeEngineAdapter:
        try:
            return self._adapters[engine_id]
        except KeyError as exc:
            raise KeyError(f"native_engine_adapter_unavailable:{engine_id}") from exc

    async def reconcile_attempt(
        self, reference: ExecutionAttemptReference
    ) -> EngineAttemptReconciliation:
        """Ask an adapter for authoritative status; unsupported/ambiguous stays unknown."""
        unknown = EngineAttemptReconciliation(
            attempt_id=reference.attempt_id,
            engine_id=reference.engine_id,
            status="unknown",
            authoritative=False,
        )
        try:
            adapter = self.get(reference.engine_id)
        except KeyError:
            return unknown
        if reference.capability not in adapter.capabilities:
            return unknown
        reconcile = getattr(adapter, "reconcile_attempt", None)
        if not callable(reconcile):
            return unknown
        try:
            async with self._semaphores[reference.engine_id]:
                result = await asyncio.wait_for(
                    reconcile(reference),
                    timeout=float(adapter.timeout_seconds),
                )
        except Exception:
            return unknown
        if not isinstance(result, EngineAttemptReconciliation):
            return unknown
        if (
            result.attempt_id != reference.attempt_id
            or result.engine_id != reference.engine_id
            or result.status == "unknown"
            or not result.authoritative
            or not str(result.evidence_ref or "").strip()
        ):
            return unknown
        return result

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "engine_id": adapter.engine_id,
                "capabilities": sorted(adapter.capabilities),
                "max_concurrency": adapter.max_concurrency,
                "timeout_seconds": adapter.timeout_seconds,
                "authority": "none",
            }
            for adapter in sorted(self._adapters.values(), key=lambda item: item.engine_id)
        ]

    async def invoke(self, engine_id: str, context: MissionTaskContext) -> EngineContribution:
        adapter = self.get(engine_id)
        if context.capability not in adapter.capabilities:
            raise ValueError(f"capability_not_registered:{engine_id}:{context.capability}")

        timeout = min(
            float(adapter.timeout_seconds),
            float(context.timeout_seconds) if context.timeout_seconds is not None else float(adapter.timeout_seconds),
        )
        semaphore = self._semaphores[engine_id]
        async with semaphore:
            try:
                result = await asyncio.wait_for(adapter.invoke(context), timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise AdapterInvocationTimeout(f"native_engine_timeout:{engine_id}:{context.task_id}") from exc

        if not isinstance(result, EngineContribution):
            raise TypeError("native_adapter_must_return_engine_contribution")
        if (
            result.engine_id != engine_id
            or result.capability != context.capability
            or result.task_id != context.task_id
            or result.mission_id != context.mission_id
            or result.workspace_id != context.workspace_id
            or result.scope_type != context.scope_type
        ):
            raise ValueError("native_adapter_result_identity_mismatch")
        if result.authority != "none":
            raise ValueError("native_adapter_attempted_authority_escalation")
        return result
