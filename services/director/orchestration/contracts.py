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
    workspace_id: str
    capability: str
    objective: str
    inputs: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: Sequence[str] = field(default_factory=tuple)
    correlation_id: str = ""
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        for name in ("task_id", "mission_id", "workspace_id", "capability", "objective"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name}_required")
            object.__setattr__(self, name, value)
        if not str(self.correlation_id or "").strip():
            raise ValueError("correlation_id_required")
        object.__setattr__(self, "correlation_id", str(self.correlation_id).strip())
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
    workspace_id: str
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

    def __post_init__(self) -> None:
        if self.status not in {"completed", "partial", "unavailable", "failed"}:
            raise ValueError("invalid_contribution_status")
        if self.authority != "none":
            raise ValueError("engine_contribution_cannot_grant_authority")
        if self.side_effect_class not in {"read_only", "external_read_only", "internal_record"}:
            raise ValueError("native_adapter_side_effect_class_not_advisory_safe")
        if self.confidence is not None and not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence_out_of_range")


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
        ):
            raise ValueError("native_adapter_result_identity_mismatch")
        if result.authority != "none":
            raise ValueError("native_adapter_attempted_authority_escalation")
        return result
