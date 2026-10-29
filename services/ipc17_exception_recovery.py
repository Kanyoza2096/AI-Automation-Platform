"""IPC-17 — objective-aware exception, recovery, and self-healing admission.

Recovery is a control-plane recommendation.  It may preserve an objective-cycle
lineage and select an existing bounded recovery path, but it never grants
execution authority or creates a second executor.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from services.autonomous_exception_recovery import (
    AutonomousExceptionRecovery,
    ExceptionObservation,
    RecoveryDisposition,
    RecoveryPlan,
)

SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class ObjectiveRecoveryContext:
    workspace_id: str
    objective_id: str
    cycle_id: str | None = None
    run_id: str | None = None
    authority: str = "none"

    def validate(self) -> None:
        if not self.workspace_id.strip() or not self.objective_id.strip():
            raise ValueError("recovery objective identity is required")
        if self.authority != "none":
            raise ValueError("recovery context cannot grant authority")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "workspace_id": self.workspace_id,
            "objective_id": self.objective_id,
            "cycle_id": self.cycle_id,
            "run_id": self.run_id,
            "authority": self.authority,
        }


def _fingerprint(value: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(value), sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def bind_recovery_to_objective(
    *,
    observation: ExceptionObservation,
    plan: RecoveryPlan,
    context: ObjectiveRecoveryContext | None,
) -> dict[str, Any]:
    """Return immutable recovery lineage; reject mismatched tenant/objective data."""
    if context is None:
        return {"authority": "none", "objective_bound": False}
    context.validate()
    if observation.workspace_id != context.workspace_id:
        raise ValueError("recovery observation workspace mismatch")
    if plan.workspace_id != context.workspace_id:
        raise ValueError("recovery plan workspace mismatch")
    if not observation.metadata.get("objective_id"):
        raise ValueError("objective-bound recovery requires objective evidence")
    if str(observation.metadata["objective_id"]) != context.objective_id:
        raise ValueError("recovery observation objective mismatch")
    if observation.metadata.get("cycle_id") and context.cycle_id and str(observation.metadata["cycle_id"]) != context.cycle_id:
        raise ValueError("recovery cycle mismatch")
    lineage = context.to_dict()
    lineage["plan_id"] = plan.plan_id
    lineage["exception_id"] = observation.exception_id
    lineage["lineage_fingerprint"] = _fingerprint(lineage)
    lineage["authority"] = "none"
    return lineage


def evaluate_objective_recovery(
    *,
    context: ObjectiveRecoveryContext | None,
    observation: ExceptionObservation,
    recovery: AutonomousExceptionRecovery | None = None,
    **kwargs: Any,
) -> tuple[RecoveryPlan, dict[str, Any]]:
    """Classify failure and bind the resulting plan to objective lineage."""
    planner = recovery or AutonomousExceptionRecovery()
    plan = planner.evaluate(observation, **kwargs)
    lineage = bind_recovery_to_objective(observation=observation, plan=plan, context=context)
    return plan, lineage


def validate_recovery_resume(
    *,
    context: ObjectiveRecoveryContext,
    stored_lineage: Mapping[str, Any],
    workspace_id: str,
    objective_id: str,
) -> None:
    """Fail closed before a recovered objective cycle is resumed."""
    context.validate()
    if workspace_id != context.workspace_id or objective_id != context.objective_id:
        raise ValueError("recovery resume objective binding mismatch")
    if stored_lineage.get("workspace_id") != context.workspace_id:
        raise ValueError("stored recovery workspace mismatch")
    if stored_lineage.get("objective_id") != context.objective_id:
        raise ValueError("stored recovery objective mismatch")
    if context.cycle_id is not None and stored_lineage.get("cycle_id") != context.cycle_id:
        raise ValueError("stored recovery cycle mismatch")
    if stored_lineage.get("authority") != "none":
        raise ValueError("stored recovery lineage cannot grant authority")
