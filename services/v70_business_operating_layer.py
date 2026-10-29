"""V70.1 — Real Business Operating Layer.

This is the first runtime-facing business layer above the V69 integration nervous
system. It gives a workspace a durable operating profile, bounded objectives, and
an end-to-end governed execution facade.

The facade deliberately has ONE execution route: BusinessAutomationEngine. It may
admit a catalog workflow, but it never calls a provider, connector, Action Kernel,
or database side-effect API directly. Consequential authority remains owned by
those existing planes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence
import hashlib
import json
import uuid

from services.business_automation_catalog import get_template
from services.business_autonomy_envelope import BusinessAutonomyEnvelope
from services.business_workflow_autonomy import (
    WorkflowAdmission,
    evaluate_workflow_admission,
    run_catalog_workflow,
)


class OperatingState(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    ACTIVE = "active"
    DEGRADED = "degraded"
    SUSPENDED = "suspended"


class ObjectiveState(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class BusinessOperatingProfile:
    workspace_id: str
    name: str
    industry: str = ""
    description: str = ""
    operating_hours: Mapping[str, Any] = field(default_factory=dict)
    policies: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    profile_id: str = field(default_factory=lambda: f"bop_{uuid.uuid4().hex[:16]}")
    state: OperatingState = OperatingState.DRAFT
    version: int = 1

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.name.strip():
            raise ValueError("workspace_id and name are required")
        if self.version < 1:
            raise ValueError("version must be >= 1")

    def activate(self) -> "BusinessOperatingProfile":
        if self.state not in {OperatingState.DRAFT, OperatingState.READY}:
            raise ValueError("profile cannot be activated from current state")
        return self.__class__(**{**self.__dict__, "state": OperatingState.ACTIVE, "version": self.version + 1})

    def with_state(self, state: OperatingState) -> "BusinessOperatingProfile":
        return self.__class__(**{**self.__dict__, "state": state, "version": self.version + 1})

    def canonical(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "industry": self.industry,
            "description": self.description,
            "operating_hours": dict(self.operating_hours),
            "policies": dict(self.policies),
            "metadata": dict(self.metadata),
            "profile_id": self.profile_id,
            "state": self.state.value,
            "version": self.version,
        }


@dataclass(frozen=True)
class BusinessObjective:
    workspace_id: str
    objective: str
    envelope: BusinessAutonomyEnvelope
    workflow_ids: tuple[str, ...]
    objective_id: str = field(default_factory=lambda: f"obj_{uuid.uuid4().hex[:16]}")
    state: ObjectiveState = ObjectiveState.DRAFT

    def __post_init__(self) -> None:
        if self.workspace_id != self.envelope.workspace_id:
            raise ValueError("objective and envelope workspace mismatch")
        if not self.objective.strip():
            raise ValueError("objective is required")
        if not self.workflow_ids:
            raise ValueError("at least one workflow is required")
        if len(set(self.workflow_ids)) != len(self.workflow_ids):
            raise ValueError("workflow_ids must be unique")

    def activate(self) -> "BusinessObjective":
        if not self.envelope.is_active():
            raise ValueError("objective envelope must be active")
        return self.__class__(**{**self.__dict__, "state": ObjectiveState.ACTIVE})

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective_id": self.objective_id,
            "workspace_id": self.workspace_id,
            "objective": self.objective,
            "workflow_ids": list(self.workflow_ids),
            "state": self.state.value,
            "envelope_id": self.envelope.envelope_id,
            "authority": "none",
            "execution_effect": "none",
        }


@dataclass(frozen=True)
class OperatingAdmission:
    workspace_id: str
    objective_id: str
    workflow_id: str
    admission: str
    action: str
    risk: str
    reasons: tuple[str, ...] = ()
    authority: str = "none"
    execution_effect: str = "none"

    def __post_init__(self) -> None:
        if self.authority != "none" or self.execution_effect != "none":
            raise ValueError("operating admission cannot grant authority")


@dataclass(frozen=True)
class OperatingRun:
    workspace_id: str
    objective_id: str
    workflow_id: str
    event_id: str
    admission: OperatingAdmission
    executions: tuple[Any, ...] = ()
    run_id: str = field(default_factory=lambda: f"run_{uuid.uuid4().hex[:16]}")

    @property
    def accepted(self) -> bool:
        return self.admission.admission == WorkflowAdmission.ADMITTED.value

    def evidence_fingerprint(self) -> str:
        payload = {
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "objective_id": self.objective_id,
            "workflow_id": self.workflow_id,
            "event_id": self.event_id,
            "admission": self.admission.admission,
            "execution_count": len(self.executions),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class BusinessOperatingLayer:
    """Workspace-scoped operating facade over the existing governed engine."""

    VERSION = "v70.1"

    def __init__(self, workspace_id: str, *, engine: Any):
        if not str(workspace_id).strip():
            raise ValueError("workspace_id is required")
        if engine is None or not callable(getattr(engine, "run_event", None)):
            raise ValueError("a BusinessAutomationEngine-compatible engine is required")
        self.workspace_id = str(workspace_id)
        self.engine = engine
        self.profile: Optional[BusinessOperatingProfile] = None
        self.objectives: dict[str, BusinessObjective] = {}

    def configure(self, profile: BusinessOperatingProfile) -> BusinessOperatingProfile:
        self._tenant(profile.workspace_id)
        self.profile = profile
        return profile

    def activate(self) -> BusinessOperatingProfile:
        if self.profile is None:
            raise ValueError("operating profile is required")
        if self.profile.state not in {OperatingState.DRAFT, OperatingState.READY}:
            raise ValueError("operating profile is not activatable")
        self.profile = self.profile.activate()
        return self.profile

    def register_objective(self, objective: BusinessObjective) -> BusinessObjective:
        self._tenant(objective.workspace_id)
        if self.profile is None or self.profile.state != OperatingState.ACTIVE:
            raise ValueError("active operating profile is required")
        if objective.state not in {ObjectiveState.DRAFT, ObjectiveState.ACTIVE}:
            raise ValueError("objective is not registerable")
        for workflow_id in objective.workflow_ids:
            template = get_template(workflow_id)
            if template is None:
                raise ValueError(f"unknown workflow: {workflow_id}")
        self.objectives[objective.objective_id] = objective
        return objective

    def plan(self, objective_id: str, *, now=None) -> tuple[OperatingAdmission, ...]:
        objective = self._objective(objective_id)
        self._tenant(objective.workspace_id)
        if objective.state not in {ObjectiveState.DRAFT, ObjectiveState.ACTIVE}:
            return tuple()
        result: list[OperatingAdmission] = []
        for workflow_id in objective.workflow_ids:
            template = get_template(workflow_id)
            if template is None:
                continue
            decision = evaluate_workflow_admission(
                objective.envelope, template, workspace_id=self.workspace_id, now=now
            )
            result.append(OperatingAdmission(
                self.workspace_id, objective.objective_id, workflow_id,
                decision.admission.value, decision.action, decision.risk,
                tuple(decision.reasons),
            ))
        return tuple(result)

    async def handle_event(
        self,
        objective_id: str,
        workflow_id: str,
        *,
        event: Mapping[str, Any],
        approved: bool = False,
        now=None,
    ) -> OperatingRun:
        objective = self._objective(objective_id)
        self._tenant(objective.workspace_id)
        if objective.state != ObjectiveState.ACTIVE:
            raise ValueError("objective is not active")
        if workflow_id not in objective.workflow_ids:
            raise ValueError("workflow is outside objective scope")
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            raise ValueError("event_id is required")
        template = get_template(workflow_id)
        if template is None:
            raise ValueError("unknown workflow")
        decision, executions = await run_catalog_workflow(
            objective.envelope, template, engine=self.engine, event=dict(event),
            workspace_id=self.workspace_id, approved=approved, now=now,
        )
        admission = OperatingAdmission(
            self.workspace_id, objective_id, workflow_id,
            decision.admission.value, decision.action, decision.risk,
            tuple(decision.reasons),
        )
        return OperatingRun(
            self.workspace_id, objective_id, workflow_id, event_id,
            admission, tuple(executions or ()),
        )

    def _objective(self, objective_id: str) -> BusinessObjective:
        objective = self.objectives.get(str(objective_id))
        if objective is None:
            raise KeyError("objective not found")
        return objective

    def _tenant(self, workspace_id: str) -> None:
        if str(workspace_id) != self.workspace_id:
            raise ValueError("workspace mismatch")


def assert_operating_layer_is_governed(layer: BusinessOperatingLayer) -> None:
    """Certification helper: the layer must have one execution dependency."""
    if not callable(getattr(layer.engine, "run_event", None)):
        raise ValueError("business operating layer requires the governed automation engine")
    forbidden = ("provider_execute", "direct_provider_call", "connector_execute", "bypass_action_kernel")
    if any(hasattr(layer, name) for name in forbidden):
        raise ValueError("parallel execution path detected")
