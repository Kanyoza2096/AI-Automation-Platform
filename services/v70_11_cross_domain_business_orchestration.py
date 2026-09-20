"""V70.11 — Cross-Domain Business Orchestration Operating System.

Coordinates already-governed business capabilities across domains. It does not
own provider I/O, policy, approvals, execution, or a second outbox. Each step
must be an explicit catalog workflow inside the caller's BusinessOperatingLayer
objective scope. Orchestration is a bounded, deterministic saga: no implicit
compensation and no AI-created authority.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Sequence
import hashlib, json

from services.business_automation_catalog import get_template
from services.v70_business_operating_layer import BusinessOperatingLayer


class OrchestrationStatus(str, Enum):
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"

class OrchestrationBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    MISSING_EVENT_ID = "missing_event_id"
    MISSING_OBJECTIVE = "missing_objective_id"
    UNKNOWN_SCENARIO = "unknown_scenario"
    EMPTY_STEPS = "empty_steps"
    UNKNOWN_WORKFLOW = "unknown_workflow"
    WORKFLOW_OUTSIDE_SCOPE = "workflow_outside_objective_scope"
    DUPLICATE_EVENT = "duplicate_event"
    TERMINAL = "terminal"
    STEP_FAILED = "step_failed"

# These are deliberately explicit business scenarios, not fuzzy AI routing.
SCENARIOS = {
    "customer.retention-risk": (
        "sales.retention-attention", "management.plan-risk-escalation",
    ),
    "customer.expansion-opportunity": (
        "sales.expansion-attention", "management.plan-attention",
    ),
    "operations.sla-breach": (
        "operations.failed-work-escalation", "management.plan-risk-escalation",
    ),
    "business.plan.risk": (
        "management.plan-risk-escalation",
    ),
}

@dataclass(frozen=True)
class OrchestrationStep:
    sequence: int
    workflow_id: str
    domain: str

@dataclass(frozen=True)
class OrchestrationState:
    workspace_id: str
    orchestration_id: str
    objective_id: str
    scenario: str
    source_event_id: str
    status: OrchestrationStatus
    current_step: int
    completed_steps: tuple[str, ...] = ()
    failed_workflow: Optional[str] = None
    version: int = 1

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps({**self.__dict__, "status": self.status.value}, sort_keys=True).encode()).hexdigest()

@dataclass(frozen=True)
class OrchestrationResult:
    accepted: bool
    state: OrchestrationState
    steps: tuple[OrchestrationStep, ...]
    executions: tuple[Any, ...] = ()
    reasons: tuple[str, ...] = ()

class CrossDomainBusinessOrchestrator:
    VERSION = "v70.11"

    def __init__(self, workspace_id: str, *, operating_layer: BusinessOperatingLayer):
        ws = str(workspace_id or "").strip()
        if not ws or operating_layer is None or operating_layer.workspace_id != ws:
            raise ValueError(OrchestrationBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.operating_layer = operating_layer
        self._states: dict[str, OrchestrationState] = {}
        self._events: set[str] = set()

    def prepare(self, *, scenario: str, event: Mapping[str, Any], objective_id: str) -> OrchestrationResult:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id: raise ValueError(OrchestrationBlock.MISSING_EVENT_ID.value)
        if str(event.get("workspace_id", self.workspace_id)) != self.workspace_id:
            raise ValueError(OrchestrationBlock.TENANT_MISMATCH.value)
        if not str(objective_id or "").strip(): raise ValueError(OrchestrationBlock.MISSING_OBJECTIVE.value)
        if scenario not in SCENARIOS: raise ValueError(OrchestrationBlock.UNKNOWN_SCENARIO.value)
        if event_id in self._events: raise ValueError(OrchestrationBlock.DUPLICATE_EVENT.value)
        workflows = SCENARIOS[scenario]
        if not workflows: raise ValueError(OrchestrationBlock.EMPTY_STEPS.value)
        objective = self.operating_layer.objectives.get(objective_id)
        if objective is None: raise ValueError(OrchestrationBlock.MISSING_OBJECTIVE.value)
        steps=[]
        for i, workflow_id in enumerate(workflows, 1):
            if get_template(workflow_id) is None: raise ValueError(OrchestrationBlock.UNKNOWN_WORKFLOW.value)
            if workflow_id not in objective.workflow_ids:
                raise ValueError(OrchestrationBlock.WORKFLOW_OUTSIDE_SCOPE.value)
            category = str(get_template(workflow_id).get("category") or "unknown").lower()
            steps.append(OrchestrationStep(i, workflow_id, category))
        oid = "kdo:orch:v70.11:" + hashlib.sha256(
            json.dumps({"workspace_id": self.workspace_id, "objective_id": objective_id,
                        "scenario": scenario, "event_id": event_id}, sort_keys=True).encode()
        ).hexdigest()[:32]
        state = OrchestrationState(self.workspace_id, oid, objective_id, scenario, event_id,
                                   OrchestrationStatus.READY, 0)
        self._states[oid] = state
        return OrchestrationResult(True, state, tuple(steps), reasons=("explicit_scenario", "governed_steps_only"))

    async def execute(self, *, orchestration_id: str, event: Mapping[str, Any], approved: bool = False, now=None) -> OrchestrationResult:
        state = self._states.get(str(orchestration_id))
        if state is None: raise ValueError(OrchestrationBlock.MISSING_OBJECTIVE.value)
        if state.status in {OrchestrationStatus.COMPLETED, OrchestrationStatus.FAILED}:
            raise ValueError(OrchestrationBlock.TERMINAL.value)
        if str(event.get("event_id") or "") != state.source_event_id:
            raise ValueError(OrchestrationBlock.MISSING_EVENT_ID.value)
        workflows = SCENARIOS[state.scenario]
        self._events.add(state.source_event_id)
        executions=[]
        completed=list(state.completed_steps)
        current=state
        for idx, workflow_id in enumerate(workflows, 1):
            if workflow_id in completed: continue
            current = OrchestrationState(self.workspace_id, state.orchestration_id, state.objective_id,
                                         state.scenario, state.source_event_id, OrchestrationStatus.RUNNING,
                                         idx - 1, tuple(completed), version=current.version + 1)
            self._states[state.orchestration_id] = current
            enriched = dict(event)
            enriched.update({"orchestration_id": state.orchestration_id,
                             "orchestration_scenario": state.scenario,
                             "orchestration_step": idx})
            run = await self.operating_layer.handle_event(state.objective_id, workflow_id,
                                                          event=enriched, approved=approved, now=now)
            executions.append(run)
            if not getattr(run, "accepted", False):
                current = OrchestrationState(self.workspace_id, state.orchestration_id, state.objective_id,
                                             state.scenario, state.source_event_id, OrchestrationStatus.BLOCKED,
                                             idx, tuple(completed), workflow_id, current.version + 1)
                self._states[state.orchestration_id] = current
                return OrchestrationResult(False, current, self._steps(state.scenario), tuple(executions),
                                           (OrchestrationBlock.STEP_FAILED.value, workflow_id))
            completed.append(workflow_id)
        current = OrchestrationState(self.workspace_id, state.orchestration_id, state.objective_id,
                                     state.scenario, state.source_event_id, OrchestrationStatus.COMPLETED,
                                     len(workflows), tuple(completed), version=current.version + 1)
        self._states[state.orchestration_id] = current
        return OrchestrationResult(True, current, self._steps(state.scenario), tuple(executions),
                                   ("cross_domain_sequence_completed",))

    def state(self, orchestration_id: str) -> Optional[OrchestrationState]:
        return self._states.get(str(orchestration_id))

    @staticmethod
    def _steps(scenario: str) -> tuple[OrchestrationStep, ...]:
        return tuple(OrchestrationStep(i, w, str(get_template(w).get("category") or "unknown").lower())
                     for i, w in enumerate(SCENARIOS[scenario], 1))

__all__ = ["CrossDomainBusinessOrchestrator", "OrchestrationStatus", "OrchestrationBlock",
           "OrchestrationStep", "OrchestrationState", "OrchestrationResult", "SCENARIOS"]
