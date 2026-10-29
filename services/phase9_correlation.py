"""Phase 9.2 correlation primitives for metrics and distributed traces.

Authority-free: these objects correlate operational evidence. They do not
execute actions, authorize principals, mutate business state, or replace the
Execution Ledger / Action Kernel / reconciliation system.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
import uuid


SCHEMA_VERSION = "phase9.correlation.v1"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class OperationalCorrelationContext:
    """Stable evidence identity shared across operational telemetry."""

    workspace_id: str
    correlation_id: str = field(default_factory=lambda: _id("corr"))
    trace_id: str = field(default_factory=lambda: _id("trace"))
    span_id: str = field(default_factory=lambda: _id("span"))
    causation_id: Optional[str] = None
    execution_id: Optional[str] = None
    incident_id: Optional[str] = None
    dependency_id: Optional[str] = None
    schema_version: str = SCHEMA_VERSION
    authority: str = "none"

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        for name in ("correlation_id", "trace_id", "span_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if self.authority != "none":
            raise ValueError("correlation context cannot grant authority")

    def child_span(self, *, causation_id: Optional[str] = None) -> "OperationalCorrelationContext":
        """Create a child trace span while preserving the root correlation/trace identity."""
        return OperationalCorrelationContext(
            workspace_id=self.workspace_id,
            correlation_id=self.correlation_id,
            trace_id=self.trace_id,
            span_id=_id("span"),
            causation_id=causation_id or self.span_id,
            execution_id=self.execution_id,
            incident_id=self.incident_id,
            dependency_id=self.dependency_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MetricObservation:
    """A metric fact carrying the same operational correlation dimensions."""

    name: str
    value: float
    observed_at: str
    context: OperationalCorrelationContext
    unit: Optional[str] = None
    attributes: Mapping[str, str] = field(default_factory=dict)
    evidence_ref: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("metric name is required")
        if not isinstance(self.value, (int, float)):
            raise TypeError("metric value must be numeric")
        if any(k.lower() in {"token", "secret", "password", "credential", "authorization"} for k in self.attributes):
            raise ValueError("sensitive credential/authorization attributes cannot be metric dimensions")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["context"] = self.context.to_dict()
        return result


@dataclass(frozen=True)
class TraceObservation:
    """A trace span observation linked to an operational correlation context."""

    operation: str
    started_at: str
    context: OperationalCorrelationContext
    ended_at: Optional[str] = None
    status: str = "UNSET"
    attributes: Mapping[str, str] = field(default_factory=dict)
    evidence_ref: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.operation.strip():
            raise ValueError("trace operation is required")
        if self.status not in {"UNSET", "OK", "ERROR", "UNKNOWN"}:
            raise ValueError("invalid trace status")
        if any(k.lower() in {"token", "secret", "password", "credential", "authorization"} for k in self.attributes):
            raise ValueError("sensitive credential/authorization attributes cannot be trace dimensions")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["context"] = self.context.to_dict()
        return result


__all__ = [
    "SCHEMA_VERSION",
    "OperationalCorrelationContext",
    "MetricObservation",
    "TraceObservation",
    "now_utc",
]
