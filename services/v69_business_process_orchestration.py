"""V69.18 — governed business-process orchestration.

Long-running process state over the V69.16 workflow plane and V69.17 event
fabric. This module coordinates state transitions only; it never executes
providers, grants authority, or bypasses policy/runtime gateways.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Mapping
from uuid import uuid4

from services.v69_business_event_fabric import BusinessEvent, MemoryEventFabric


class ProcessState(str, Enum):
    RUNNABLE="runnable"; WAITING_EVENT="waiting_event"; WAITING_TIMER="waiting_timer"
    WAITING_HUMAN="waiting_human"; ESCALATED="escalated"; COMPLETED="completed"
    FAILED="failed"; COMPENSATING="compensating"; COMPENSATED="compensated"; BLOCKED="blocked"

class ProcessBlock(str, Enum):
    TENANT_MISMATCH="tenant_mismatch"; INVALID_TRANSITION="invalid_transition"
    EVENT_MISMATCH="event_mismatch"; TIMER_NOT_DUE="timer_not_due"; HUMAN_REQUIRED="human_required"
    ESCALATION_INVALID="escalation_invalid"; DUPLICATE_INSTANCE="duplicate_instance"

@dataclass(frozen=True)
class ProcessTransition:
    transition_id: str
    from_state: ProcessState
    to_state: ProcessState
    trigger: str
    target: str | None = None
    timeout_seconds: int | None = None
    escalation_target: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class BusinessProcessDefinition:
    process_id: str
    workspace_id: str
    name: str
    initial_state: ProcessState
    terminal_states: frozenset[ProcessState] = frozenset({ProcessState.COMPLETED, ProcessState.COMPENSATED})
    transitions: tuple[ProcessTransition, ...] = ()
    version: str = "1.0"

    def __post_init__(self):
        if not self.process_id.strip() or not self.workspace_id.strip() or not self.name.strip():
            raise ValueError("invalid_process_definition")
        for t in self.transitions:
            if t.timeout_seconds is not None and t.timeout_seconds < 1:
                raise ValueError("invalid_timeout")
            if t.from_state == t.to_state and t.trigger not in {"event", "timer", "human"}:
                raise ValueError("invalid_transition")

@dataclass(frozen=True)
class ProcessInstance:
    instance_id: str
    definition: BusinessProcessDefinition
    state: ProcessState
    correlation_id: str
    current_target: str | None = None
    deadline: datetime | None = None
    escalation_target: str | None = None
    consumed_event_ids: frozenset[str] = frozenset()
    history: tuple[str, ...] = ()
    context: Mapping[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class ProcessDecision:
    accepted: bool
    instance: ProcessInstance
    reason_codes: tuple[str, ...] = ()
    emitted_events: tuple[BusinessEvent, ...] = ()

class BusinessProcessOrchestrator:
    """Deterministic process state machine bound to one tenant."""
    def __init__(self, workspace_id: str, event_fabric: MemoryEventFabric | None = None):
        if not workspace_id.strip(): raise ValueError(ProcessBlock.TENANT_MISMATCH.value)
        self.workspace_id = workspace_id
        self.fabric = event_fabric
        self._instances: dict[str, ProcessInstance] = {}

    def start(self, definition: BusinessProcessDefinition, *, correlation_id: str | None = None, context: Mapping[str, object] | None = None) -> ProcessInstance:
        if definition.workspace_id != self.workspace_id: raise ValueError(ProcessBlock.TENANT_MISMATCH.value)
        i = ProcessInstance(str(uuid4()), definition, definition.initial_state, correlation_id or str(uuid4()), context=context or {})
        self._instances[i.instance_id] = i
        return i

    def on_event(self, instance: ProcessInstance, event: BusinessEvent) -> ProcessDecision:
        if instance.definition.workspace_id != self.workspace_id or event.workspace_id != self.workspace_id:
            return ProcessDecision(False, instance, (ProcessBlock.TENANT_MISMATCH.value,))
        if event.event_id in instance.consumed_event_ids:
            return ProcessDecision(False, instance, ("duplicate_event",))
        candidates = [t for t in instance.definition.transitions if t.from_state == instance.state and t.trigger == "event" and (t.target is None or t.target == event.event_type)]
        if not candidates:
            return ProcessDecision(False, instance, (ProcessBlock.EVENT_MISMATCH.value,))
        t = candidates[0]
        nxt = self._advance(instance, t, event_id=event.event_id)
        return ProcessDecision(True, nxt)

    def human_approve(self, instance: ProcessInstance, *, actor: str, decision: str = "approve") -> ProcessDecision:
        if instance.state != ProcessState.WAITING_HUMAN:
            return ProcessDecision(False, instance, (ProcessBlock.HUMAN_REQUIRED.value,))
        if not actor.strip(): return ProcessDecision(False, instance, ("actor_required",))
        trigger = "human_approve" if decision == "approve" else "human_reject"
        candidates = [t for t in instance.definition.transitions if t.from_state == instance.state and t.trigger == trigger]
        if not candidates: return ProcessDecision(False, instance, (ProcessBlock.INVALID_TRANSITION.value,))
        return ProcessDecision(True, self._advance(instance, candidates[0], actor=actor))

    def tick(self, instance: ProcessInstance, *, now: datetime | None = None) -> ProcessDecision:
        now = now or datetime.now(timezone.utc)
        if instance.state != ProcessState.WAITING_TIMER or instance.deadline is None:
            return ProcessDecision(False, instance, (ProcessBlock.TIMER_NOT_DUE.value,))
        if now < instance.deadline: return ProcessDecision(False, instance, (ProcessBlock.TIMER_NOT_DUE.value,))
        candidates = [t for t in instance.definition.transitions if t.from_state == instance.state and t.trigger == "timer"]
        if not candidates: return ProcessDecision(False, instance, (ProcessBlock.INVALID_TRANSITION.value,))
        return ProcessDecision(True, self._advance(instance, candidates[0]))

    def escalate(self, instance: ProcessInstance) -> ProcessDecision:
        if instance.state not in {ProcessState.WAITING_HUMAN, ProcessState.WAITING_TIMER}:
            return ProcessDecision(False, instance, (ProcessBlock.ESCALATION_INVALID.value,))
        target = instance.escalation_target
        if not target:
            return ProcessDecision(False, instance, (ProcessBlock.ESCALATION_INVALID.value,))
        return ProcessDecision(True, replace(instance, state=ProcessState.ESCALATED, history=instance.history + (f"escalated:{target}",)))

    def _advance(self, instance: ProcessInstance, t: ProcessTransition, *, event_id: str | None = None, actor: str | None = None) -> ProcessInstance:
        if t.from_state != instance.state: raise ValueError(ProcessBlock.INVALID_TRANSITION.value)
        deadline = None
        if t.timeout_seconds is not None:
            deadline = datetime.now(timezone.utc) + timedelta(seconds=t.timeout_seconds)
        state = t.to_state
        consumed = instance.consumed_event_ids | ({event_id} if event_id else set())
        history = instance.history + (f"{t.trigger}:{instance.state.value}->{state.value}",)
        if actor: history += (f"actor:{actor}",)
        out = replace(instance, state=state, current_target=t.target, deadline=deadline,
                      escalation_target=t.escalation_target, consumed_event_ids=frozenset(consumed), history=history)
        self._instances[out.instance_id] = out
        return out

    def get(self, instance_id: str) -> ProcessInstance | None:
        i = self._instances.get(instance_id)
        return i if i and i.definition.workspace_id == self.workspace_id else None

__all__ = ["ProcessState", "ProcessBlock", "ProcessTransition", "BusinessProcessDefinition", "ProcessInstance", "ProcessDecision", "BusinessProcessOrchestrator"]
