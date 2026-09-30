"""V70.4 — Canonical Runtime & Intelligence Reconciliation.

This is a reconciliation/admission layer, not a new execution engine.
It establishes one vocabulary for:

    event -> business context -> capability -> governance -> action -> execution
    -> provider result -> business outcome -> learning

The module deliberately delegates side effects to the existing BusinessAutomation
engine / Action Kernel / durable outbox. It never calls a provider directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Mapping, Sequence


class CapabilityResolution(str, Enum):
    EXECUTABLE = "executable"
    ADVISORY_ONLY = "advisory_only"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"


@dataclass(frozen=True)
class CanonicalEventEnvelope:
    event_id: str
    workspace_id: str
    event_type: str
    source: str
    payload: Mapping[str, Any]
    correlation_id: str
    causation_id: str | None = None
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def validate(self) -> None:
        for name in ("event_id", "workspace_id", "event_type", "source", "correlation_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if not isinstance(self.payload, Mapping):
            raise ValueError("payload must be a mapping")


@dataclass(frozen=True)
class CapabilityResolutionResult:
    recommendation: str
    status: CapabilityResolution
    workflow_id: str | None = None
    action: str | None = None
    reasons: tuple[str, ...] = ()

    @property
    def executable(self) -> bool:
        return self.status == CapabilityResolution.EXECUTABLE


@dataclass(frozen=True)
class CanonicalActionIdentity:
    workspace_id: str
    workflow_id: str
    action_type: str
    event_id: str
    correlation_id: str
    idempotency_key: str

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        workflow_id: str,
        action_type: str,
        event_id: str,
        correlation_id: str,
    ) -> "CanonicalActionIdentity":
        values = [workspace_id.strip(), workflow_id.strip(), action_type.strip(), event_id.strip()]
        if not all(values):
            raise ValueError("workspace_id, workflow_id, action_type and event_id are required")
        canonical = json.dumps(values, separators=(",", ":"), ensure_ascii=True)
        key = "kdo:v70.4:" + hashlib.sha256(canonical.encode()).hexdigest()
        return cls(workspace_id.strip(), workflow_id.strip(), action_type.strip(), event_id.strip(), correlation_id.strip(), key)


@dataclass(frozen=True)
class OutcomeLink:
    objective_id: str
    workflow_id: str
    action_id: str
    outcome_type: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        for name in ("objective_id", "workflow_id", "action_id", "outcome_type"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")


class CanonicalRuntimeReconciler:
    """Read-only reconciliation facade over the existing runtime components."""

    VERSION = "70.4.0"

    def __init__(
        self,
        *,
        catalog: Sequence[Mapping[str, Any]],
        executable_actions: Sequence[str] = (),
        approved_aliases: Mapping[str, str] | None = None,
    ) -> None:
        self._catalog = {str(item.get("id")): dict(item) for item in catalog if str(item.get("id") or "").strip()}
        self._actions = {str(x).strip() for x in executable_actions if str(x).strip()}
        # Aliases are explicit architecture decisions. No fuzzy/LLM-generated
        # mapping is permitted because that would manufacture authority.
        self._aliases = {str(k).strip(): str(v).strip() for k, v in (approved_aliases or {}).items()}

    def resolve_capability(self, recommendation: str) -> CapabilityResolutionResult:
        requested = str(recommendation or "").strip()
        if not requested:
            return CapabilityResolutionResult("", CapabilityResolution.CAPABILITY_UNAVAILABLE, reasons=("recommendation_missing",))

        workflow_id = self._aliases.get(requested, requested)
        workflow = self._catalog.get(workflow_id)
        if workflow is None:
            return CapabilityResolutionResult(
                requested,
                CapabilityResolution.CAPABILITY_UNAVAILABLE,
                reasons=("workflow_capability_not_registered",),
            )

        action = str(workflow.get("action") or "").strip()
        if not action:
            return CapabilityResolutionResult(
                requested,
                CapabilityResolution.ADVISORY_ONLY,
                workflow_id=workflow_id,
                reasons=("workflow_has_no_executable_action",),
            )
        if action not in self._actions:
            return CapabilityResolutionResult(
                requested,
                CapabilityResolution.ADVISORY_ONLY,
                workflow_id=workflow_id,
                action=action,
                reasons=("action_not_registered_in_execution_capability_set",),
            )
        return CapabilityResolutionResult(
            requested,
            CapabilityResolution.EXECUTABLE,
            workflow_id=workflow_id,
            action=action,
        )

    def canonical_action(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        event_id: str,
        correlation_id: str,
    ) -> CanonicalActionIdentity:
        workflow = self._catalog.get(workflow_id)
        if workflow is None:
            raise ValueError("workflow capability is not registered")
        action = str(workflow.get("action") or "").strip()
        if not action:
            raise ValueError("workflow has no executable action")
        return CanonicalActionIdentity.create(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            action_type=action,
            event_id=event_id,
            correlation_id=correlation_id,
        )

    def validate_recommendation_boundary(self, recommendation: str) -> CapabilityResolutionResult:
        """Enforce the V70.4 AI-to-execution boundary.

        A missing capability is never converted into an invented executable
        workflow. Callers may present the result to a human as advisory work.
        """
        return self.resolve_capability(recommendation)


__all__ = [
    "CapabilityResolution",
    "CanonicalEventEnvelope",
    "CapabilityResolutionResult",
    "CanonicalActionIdentity",
    "OutcomeLink",
    "CanonicalRuntimeReconciler",
]
