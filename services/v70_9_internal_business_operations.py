"""V70.9 — Internal Business Operations Operating System.

Owns internal operational state, workload/SLA evidence, and governed routing only.
It deliberately reuses BusinessOperatingLayer and the existing governed catalog;
it never executes providers, connectors, or Action Kernel calls directly.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional
from datetime import datetime, timedelta, timezone
import hashlib, json

from services.v70_business_operating_layer import BusinessOperatingLayer


class WorkStatus(str, Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class OperationsBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    MISSING_WORK_ID = "missing_work_id"
    MISSING_EVENT_ID = "missing_event_id"
    MISSING_EVENT_NAME = "missing_event_name"
    DUPLICATE_EVENT = "duplicate_event"
    UNKNOWN_EVENT = "unknown_event"
    TERMINAL = "terminal"
    INVALID_TRANSITION = "invalid_transition"
    INVALID_PRIORITY = "invalid_priority"
    INVALID_SLA = "invalid_sla"


EVENTS = {
    "work.created": WorkStatus.OPEN,
    "work.assigned": WorkStatus.ASSIGNED,
    "work.started": WorkStatus.IN_PROGRESS,
    "work.blocked": WorkStatus.BLOCKED,
    "work.resumed": WorkStatus.IN_PROGRESS,
    "work.completed": WorkStatus.COMPLETED,
    "work.failed": WorkStatus.FAILED,
    "work.cancelled": WorkStatus.CANCELLED,
    "work.sla_warning": None,
    "work.sla_breached": WorkStatus.BLOCKED,
}

ALLOWED = {
    WorkStatus.OPEN: {WorkStatus.OPEN, WorkStatus.ASSIGNED, WorkStatus.IN_PROGRESS, WorkStatus.BLOCKED, WorkStatus.COMPLETED, WorkStatus.FAILED, WorkStatus.CANCELLED},
    WorkStatus.ASSIGNED: {WorkStatus.ASSIGNED, WorkStatus.IN_PROGRESS, WorkStatus.CANCELLED},
    WorkStatus.IN_PROGRESS: {WorkStatus.IN_PROGRESS, WorkStatus.BLOCKED, WorkStatus.COMPLETED, WorkStatus.FAILED, WorkStatus.CANCELLED},
    WorkStatus.BLOCKED: {WorkStatus.BLOCKED, WorkStatus.IN_PROGRESS, WorkStatus.CANCELLED, WorkStatus.FAILED},
    WorkStatus.COMPLETED: {WorkStatus.COMPLETED},
    WorkStatus.FAILED: {WorkStatus.FAILED, WorkStatus.IN_PROGRESS, WorkStatus.CANCELLED},
    WorkStatus.CANCELLED: {WorkStatus.CANCELLED},
}

PRIORITY_WEIGHT = {WorkPriority.LOW: 10, WorkPriority.MEDIUM: 25, WorkPriority.HIGH: 50, WorkPriority.CRITICAL: 100}
DEFAULT_SLA_HOURS = {WorkPriority.LOW: 72, WorkPriority.MEDIUM: 24, WorkPriority.HIGH: 8, WorkPriority.CRITICAL: 2}


@dataclass(frozen=True)
class InternalWorkSnapshot:
    workspace_id: str
    work_id: str
    status: WorkStatus
    priority: WorkPriority
    owner_id: Optional[str]
    title: str
    category: str
    created_at: str
    due_at: str
    last_event_id: str
    last_event_name: str
    sla_breached: bool = False
    version: int = 1

    def fingerprint(self) -> str:
        payload = {**self.__dict__, "status": self.status.value, "priority": self.priority.value}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class OperationsResult:
    accepted: bool
    work_id: str
    status: WorkStatus
    priority: WorkPriority
    event_id: str
    workflow_id: Optional[str]
    sla_breached: bool
    reasons: tuple[str, ...] = ()
    snapshot: Optional[InternalWorkSnapshot] = None
    execution: Any = None


class InternalBusinessOperations:
    VERSION = "v70.9"
    WORKFLOWS = {
        "attention": "operations.internal-attention",
        "blocked": "operations.blocked-work",
        "failed": "operations.failed-work",
        "sla": "operations.sla-breach",
    }

    def __init__(self, workspace_id: str, *, operating_layer: BusinessOperatingLayer):
        if not str(workspace_id).strip():
            raise ValueError("workspace_id is required")
        if operating_layer is None or operating_layer.workspace_id != str(workspace_id):
            raise ValueError(OperationsBlock.TENANT_MISMATCH.value)
        self.workspace_id = str(workspace_id)
        self.operating_layer = operating_layer
        self._snapshots: dict[str, InternalWorkSnapshot] = {}
        self._events: set[str] = set()

    def process(self, *, event: Mapping[str, Any], objective_id: str, now=None) -> OperationsResult:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            raise ValueError(OperationsBlock.MISSING_EVENT_ID.value)
        if event_id in self._events:
            raise ValueError(OperationsBlock.DUPLICATE_EVENT.value)
        if str(event.get("workspace_id", self.workspace_id)) != self.workspace_id:
            raise ValueError(OperationsBlock.TENANT_MISMATCH.value)
        work_id = str(event.get("work_id") or "").strip()
        if not work_id:
            raise ValueError(OperationsBlock.MISSING_WORK_ID.value)
        name = str(event.get("event_name") or "").strip()
        if not name:
            raise ValueError(OperationsBlock.MISSING_EVENT_NAME.value)
        if name not in EVENTS:
            raise ValueError(OperationsBlock.UNKNOWN_EVENT.value)

        previous = self._snapshots.get(work_id)
        priority_raw = str(event.get("priority") or (previous.priority.value if previous else "medium")).lower()
        try:
            priority = WorkPriority(priority_raw)
        except ValueError as exc:
            raise ValueError(OperationsBlock.INVALID_PRIORITY.value) from exc

        if previous:
            if previous.status in {WorkStatus.COMPLETED, WorkStatus.CANCELLED} and EVENTS[name] != previous.status:
                raise ValueError(OperationsBlock.TERMINAL.value)
            target = EVENTS[name] or previous.status
            if target not in ALLOWED[previous.status]:
                raise ValueError(OperationsBlock.INVALID_TRANSITION.value)
        else:
            if name != "work.created":
                raise ValueError(OperationsBlock.INVALID_TRANSITION.value)
            target = WorkStatus.OPEN

        current = self._now(now)
        if previous:
            created_at = previous.created_at
            due_at = previous.due_at
        else:
            created_at = current.isoformat()
            sla_hours = event.get("sla_hours", DEFAULT_SLA_HOURS[priority])
            try:
                sla_hours = float(sla_hours)
            except (TypeError, ValueError) as exc:
                raise ValueError(OperationsBlock.INVALID_SLA.value) from exc
            if sla_hours <= 0 or sla_hours > 8760:
                raise ValueError(OperationsBlock.INVALID_SLA.value)
            due_at = (current + timedelta(hours=sla_hours)).isoformat()

        try:
            due = datetime.fromisoformat(due_at)
        except ValueError as exc:
            raise ValueError(OperationsBlock.INVALID_SLA.value) from exc
        breached = bool(previous.sla_breached if previous else False) or (current >= due and target not in {WorkStatus.COMPLETED, WorkStatus.CANCELLED})
        if name == "work.sla_breached":
            breached = True

        owner_id = str(event.get("owner_id") or (previous.owner_id if previous else "")).strip() or None
        title = str(event.get("title") or (previous.title if previous else "Internal work")).strip()
        category = str(event.get("category") or (previous.category if previous else "operations")).strip()
        reasons = [f"event:{name}", f"priority:{priority.value}"]
        if owner_id:
            reasons.append("owner_assigned")
        if breached:
            reasons.append("sla_breached")
        if target == WorkStatus.BLOCKED:
            reasons.append("blocked_work_requires_attention")
        if target == WorkStatus.FAILED:
            reasons.append("failed_work_requires_attention")

        workflow = None
        if breached:
            workflow = self.WORKFLOWS["sla"]
        elif target == WorkStatus.BLOCKED:
            workflow = self.WORKFLOWS["blocked"]
        elif target == WorkStatus.FAILED:
            workflow = self.WORKFLOWS["failed"]
        elif target in {WorkStatus.OPEN, WorkStatus.ASSIGNED}:
            workflow = self.WORKFLOWS["attention"]

        snapshot = InternalWorkSnapshot(self.workspace_id, work_id, target, priority, owner_id, title, category,
                                        created_at, due_at, event_id, name, breached, previous.version + 1 if previous else 1)
        self._events.add(event_id)
        self._snapshots[work_id] = snapshot
        return OperationsResult(True, work_id, target, priority, event_id, workflow, breached, tuple(reasons), snapshot)

    async def handle_event(self, *, event: Mapping[str, Any], objective_id: str, approved: bool = False, now=None) -> OperationsResult:
        result = self.process(event=event, objective_id=objective_id, now=now)
        if not result.workflow_id:
            return result
        enriched = dict(event)
        enriched.update({"internal_work_status": result.status.value, "internal_work_priority": result.priority.value,
                         "internal_work_sla_breached": result.sla_breached, "internal_work_reasons": list(result.reasons)})
        run = await self.operating_layer.handle_event(objective_id, result.workflow_id, event=enriched, approved=approved, now=now)
        return OperationsResult(result.accepted, result.work_id, result.status, result.priority, result.event_id,
                                result.workflow_id, result.sla_breached, result.reasons, result.snapshot, run)

    def snapshot(self, work_id: str) -> Optional[InternalWorkSnapshot]:
        return self._snapshots.get(str(work_id).strip())

    def all_snapshots(self) -> tuple[InternalWorkSnapshot, ...]:
        return tuple(self._snapshots.values())

    @staticmethod
    def _now(value=None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        raise TypeError("now must be datetime or None")


__all__ = ["InternalBusinessOperations", "InternalWorkSnapshot", "OperationsResult", "WorkStatus", "WorkPriority", "OperationsBlock"]
