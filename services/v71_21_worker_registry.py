"""V71.21 — Governed Worker Registry & Lifecycle Control.

The registry is the authoritative inventory of operational worker instances and
worker classes. It does not grant authorization and never executes actions.
Identity remains in V71.1; capability permission remains in V71.2; capacity and
selection remain in V71.9; execution remains downstream in governance/Action
Kernel.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json

from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_9_worker_selection import WorkerCapacityRegistry, WorkerAvailability


class WorkerRegistryBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    WORKER_NOT_FOUND = "worker_not_found"
    DUPLICATE_WORKER = "duplicate_worker"
    INVALID_WORKER = "invalid_worker"
    INVALID_TRANSITION = "invalid_worker_transition"
    IDENTITY_NOT_ACTIVE = "worker_identity_not_active"
    HEALTH_HOLD = "worker_health_hold"
    CAPACITY_INVALID = "worker_capacity_invalid"
    RETIRED = "worker_retired"
    REVOKED = "worker_revoked"
    WORKER_TAMPERED = "worker_registry_tampered"
    POOL_NOT_FOUND = "worker_pool_not_found"
    DUPLICATE_POOL = "duplicate_worker_pool"
    INVALID_POOL = "invalid_worker_pool"
    POOL_TAMPERED = "worker_pool_tampered"


class WorkerKind(str, Enum):
    DETERMINISTIC = "deterministic_service"
    WORKFLOW = "workflow_worker"
    AI = "ai_worker"
    CONNECTOR = "connector_worker"
    SCHEDULED = "scheduled_worker"
    EVENT_DRIVEN = "event_driven_worker"


class WorkerLifecycle(str, Enum):
    REGISTERED = "registered"
    READY = "ready"
    ACTIVE = "active"
    DRAINING = "draining"
    SUSPENDED = "suspended"
    RETIRED = "retired"
    REVOKED = "revoked"


class WorkerHealth(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


_ALLOWED = {
    WorkerLifecycle.REGISTERED: {WorkerLifecycle.READY, WorkerLifecycle.SUSPENDED, WorkerLifecycle.RETIRED, WorkerLifecycle.REVOKED},
    WorkerLifecycle.READY: {WorkerLifecycle.ACTIVE, WorkerLifecycle.SUSPENDED, WorkerLifecycle.DRAINING, WorkerLifecycle.RETIRED, WorkerLifecycle.REVOKED},
    WorkerLifecycle.ACTIVE: {WorkerLifecycle.DRAINING, WorkerLifecycle.SUSPENDED, WorkerLifecycle.RETIRED, WorkerLifecycle.REVOKED},
    WorkerLifecycle.DRAINING: {WorkerLifecycle.READY, WorkerLifecycle.SUSPENDED, WorkerLifecycle.RETIRED, WorkerLifecycle.REVOKED},
    WorkerLifecycle.SUSPENDED: {WorkerLifecycle.READY, WorkerLifecycle.RETIRED, WorkerLifecycle.REVOKED},
    WorkerLifecycle.RETIRED: set(),
    WorkerLifecycle.REVOKED: set(),
}


@dataclass(frozen=True)
class WorkerPool:
    pool_id: str
    workspace_id: str
    name: str
    worker_kind: WorkerKind
    capability_profile: tuple[str, ...] = ()
    min_instances: int = 0
    desired_instances: int = 0
    max_instances: int = 1
    metadata: Mapping[str, Any] = field(default_factory=dict)
    pool_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "pool_id": self.pool_id, "workspace_id": self.workspace_id,
            "name": self.name, "worker_kind": self.worker_kind.value,
            "capability_profile": sorted(set(self.capability_profile)),
            "min_instances": self.min_instances, "desired_instances": self.desired_instances,
            "max_instances": self.max_instances, "metadata": dict(self.metadata),
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class WorkerRecord:
    worker_id: str
    workspace_id: str
    pool_id: str
    name: str
    worker_kind: WorkerKind
    lifecycle: WorkerLifecycle = WorkerLifecycle.REGISTERED
    health: WorkerHealth = WorkerHealth.UNKNOWN
    version: str = "1"
    declared_capabilities: tuple[str, ...] = ()
    max_concurrent_tasks: int = 1
    active_tasks: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)
    registry_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id, "workspace_id": self.workspace_id,
            "pool_id": self.pool_id, "name": self.name,
            "worker_kind": self.worker_kind.value, "lifecycle": self.lifecycle.value,
            "health": self.health.value, "version": self.version,
            "declared_capabilities": sorted(set(self.declared_capabilities)),
            "max_concurrent_tasks": self.max_concurrent_tasks, "active_tasks": self.active_tasks,
            "metadata": dict(self.metadata),
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()

    @property
    def eligible_for_selection(self) -> bool:
        return self.lifecycle == WorkerLifecycle.ACTIVE and self.health in {WorkerHealth.HEALTHY, WorkerHealth.DEGRADED} and self.active_tasks < self.max_concurrent_tasks


class WorkerRegistry:
    VERSION = "v71.21"

    def __init__(self, workspace_id: str, identity_registry: AgentIdentityRegistry,
                 capacity_registry: Optional[WorkerCapacityRegistry] = None):
        ws = str(workspace_id or "").strip()
        if not ws or identity_registry.workspace_id != ws or (capacity_registry is not None and capacity_registry.workspace_id != ws):
            raise ValueError(WorkerRegistryBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.identity_registry = identity_registry
        self.capacity_registry = capacity_registry
        self._workers: dict[str, WorkerRecord] = {}
        self._pools: dict[str, WorkerPool] = {}

    def create_pool(self, *, pool_id: str, name: str, worker_kind: WorkerKind,
                    capability_profile: tuple[str, ...] = (), min_instances: int = 0,
                    desired_instances: int = 0, max_instances: int = 1,
                    metadata: Optional[Mapping[str, Any]] = None) -> WorkerPool:
        pid = str(pool_id or "").strip()
        if not pid or pid in self._pools or not str(name or "").strip():
            raise ValueError(WorkerRegistryBlock.DUPLICATE_POOL.value if pid in self._pools else WorkerRegistryBlock.INVALID_POOL.value)
        try:
            mn, desired, mx = int(min_instances), int(desired_instances), int(max_instances)
            kind = WorkerKind(worker_kind)
        except (TypeError, ValueError):
            raise ValueError(WorkerRegistryBlock.INVALID_POOL.value)
        if mn < 0 or desired < mn or mx < desired or mx < 1:
            raise ValueError(WorkerRegistryBlock.INVALID_POOL.value)
        pool = WorkerPool(pid, self.workspace_id, str(name).strip(), kind,
                          tuple(sorted({str(x).strip() for x in capability_profile if str(x).strip()})),
                          mn, desired, mx, dict(metadata or {}), "")
        pool = WorkerPool(**{**pool.__dict__, "pool_fingerprint": pool.fingerprint()})
        self._pools[pid] = pool
        return pool

    def get_pool(self, pool_id: str) -> Optional[WorkerPool]:
        return self._pools.get(str(pool_id))

    def assert_pool_integrity(self, pool: WorkerPool) -> None:
        if pool.workspace_id != self.workspace_id:
            raise ValueError(WorkerRegistryBlock.TENANT_MISMATCH.value)
        if pool.pool_fingerprint != pool.fingerprint():
            raise ValueError(WorkerRegistryBlock.POOL_TAMPERED.value)

    def register(self, *, worker_id: str, pool_id: str, name: str, worker_kind: WorkerKind,
                 version: str = "1", declared_capabilities: tuple[str, ...] = (),
                 health: WorkerHealth = WorkerHealth.UNKNOWN, max_concurrent_tasks: int = 1,
                 active_tasks: int = 0, metadata: Optional[Mapping[str, Any]] = None) -> WorkerRecord:
        wid = str(worker_id or "").strip()
        if not wid or wid in self._workers or not str(name or "").strip():
            raise ValueError(WorkerRegistryBlock.DUPLICATE_WORKER.value if wid in self._workers else WorkerRegistryBlock.INVALID_WORKER.value)
        pool = self.get_pool(pool_id)
        if pool is None:
            raise ValueError(WorkerRegistryBlock.POOL_NOT_FOUND.value)
        self.assert_pool_integrity(pool)
        identity = self.identity_registry.get(wid)
        if identity is None:
            raise ValueError(WorkerRegistryBlock.WORKER_NOT_FOUND.value)
        self.identity_registry.assert_integrity(identity)
        try:
            kind, h = WorkerKind(worker_kind), WorkerHealth(health)
            max_c, active = int(max_concurrent_tasks), int(active_tasks)
        except (TypeError, ValueError):
            raise ValueError(WorkerRegistryBlock.INVALID_WORKER.value)
        caps = tuple(sorted({str(x).strip() for x in declared_capabilities if str(x).strip()}))
        if max_c < 1 or active < 0 or active > max_c or not str(version or "").strip() or not set(pool.capability_profile).issubset(set(caps)):
            raise ValueError(WorkerRegistryBlock.INVALID_WORKER.value)
        if kind != pool.worker_kind:
            raise ValueError(WorkerRegistryBlock.INVALID_WORKER.value)
        record = WorkerRecord(wid, self.workspace_id, pool.pool_id, str(name).strip(), kind,
                              WorkerLifecycle.REGISTERED, h, str(version).strip(), caps, max_c, active, dict(metadata or {}), "")
        record = WorkerRecord(**{**record.__dict__, "registry_fingerprint": record.fingerprint()})
        self._workers[wid] = record
        return record

    def get(self, worker_id: str) -> Optional[WorkerRecord]:
        return self._workers.get(str(worker_id))

    def list_workers(self, *, lifecycle: Optional[WorkerLifecycle] = None,
                     worker_kind: Optional[WorkerKind] = None) -> tuple[WorkerRecord, ...]:
        rows = tuple(self._workers.values())
        if lifecycle is not None:
            rows = tuple(x for x in rows if x.lifecycle == WorkerLifecycle(lifecycle))
        if worker_kind is not None:
            rows = tuple(x for x in rows if x.worker_kind == WorkerKind(worker_kind))
        return tuple(sorted(rows, key=lambda x: x.worker_id))

    def assert_integrity(self, worker: WorkerRecord) -> None:
        if worker.workspace_id != self.workspace_id:
            raise ValueError(WorkerRegistryBlock.TENANT_MISMATCH.value)
        if worker.registry_fingerprint != worker.fingerprint():
            raise ValueError(WorkerRegistryBlock.WORKER_TAMPERED.value)

    def _replace(self, worker: WorkerRecord, **changes: Any) -> WorkerRecord:
        self.assert_integrity(worker)
        updated = WorkerRecord(**{**worker.__dict__, **changes, "registry_fingerprint": ""})
        updated = WorkerRecord(**{**updated.__dict__, "registry_fingerprint": updated.fingerprint()})
        self._workers[worker.worker_id] = updated
        return updated

    def transition(self, *, worker_id: str, lifecycle: WorkerLifecycle) -> WorkerRecord:
        worker = self.get(worker_id)
        if worker is None:
            raise ValueError(WorkerRegistryBlock.WORKER_NOT_FOUND.value)
        self.assert_integrity(worker)
        target = WorkerLifecycle(lifecycle)
        if target not in _ALLOWED[worker.lifecycle]:
            if worker.lifecycle == WorkerLifecycle.RETIRED:
                raise ValueError(WorkerRegistryBlock.RETIRED.value)
            if worker.lifecycle == WorkerLifecycle.REVOKED:
                raise ValueError(WorkerRegistryBlock.REVOKED.value)
            raise ValueError(WorkerRegistryBlock.INVALID_TRANSITION.value)
        if target in {WorkerLifecycle.READY, WorkerLifecycle.ACTIVE}:
            identity = self.identity_registry.get(worker.worker_id)
            if identity is None:
                raise ValueError(WorkerRegistryBlock.WORKER_NOT_FOUND.value)
            self.identity_registry.assert_integrity(identity)
            if identity.status != AgentStatus.ACTIVE:
                raise ValueError(WorkerRegistryBlock.IDENTITY_NOT_ACTIVE.value)
            if worker.health == WorkerHealth.UNAVAILABLE:
                raise ValueError(WorkerRegistryBlock.HEALTH_HOLD.value)
        if target == WorkerLifecycle.ACTIVE and self.capacity_registry is not None:
            cap = self.capacity_registry.get(worker.worker_id)
            if cap is None:
                raise ValueError(WorkerRegistryBlock.CAPACITY_INVALID.value)
            self.capacity_registry.assert_integrity(cap)
            if cap.availability in {WorkerAvailability.UNAVAILABLE, WorkerAvailability.DRAINING} or cap.safety_posture in {"hold", "suspended", "emergency"}:
                raise ValueError(WorkerRegistryBlock.CAPACITY_INVALID.value)
        if target in {WorkerLifecycle.RETIRED, WorkerLifecycle.REVOKED} and self.capacity_registry is not None:
            cap = self.capacity_registry.get(worker.worker_id)
            if cap is not None:
                self.capacity_registry.assert_integrity(cap)
                self.capacity_registry.update_load(worker.worker_id, active_tasks=cap.active_tasks, load_score=cap.load_score, availability=WorkerAvailability.UNAVAILABLE)
        return self._replace(worker, lifecycle=target)

    def set_health(self, *, worker_id: str, health: WorkerHealth) -> WorkerRecord:
        worker = self.get(worker_id)
        if worker is None:
            raise ValueError(WorkerRegistryBlock.WORKER_NOT_FOUND.value)
        self.assert_integrity(worker)
        target = WorkerHealth(health)
        updated = self._replace(worker, health=target)
        if self.capacity_registry is not None:
            cap = self.capacity_registry.get(worker_id)
            if cap is not None:
                self.capacity_registry.assert_integrity(cap)
                availability = WorkerAvailability.UNAVAILABLE if target == WorkerHealth.UNAVAILABLE else cap.availability
                self.capacity_registry.update_load(worker_id, active_tasks=cap.active_tasks, load_score=cap.load_score, availability=availability)
        return updated

    def set_capacity(self, *, worker_id: str, active_tasks: int, max_concurrent_tasks: Optional[int] = None) -> WorkerRecord:
        worker = self.get(worker_id)
        if worker is None:
            raise ValueError(WorkerRegistryBlock.WORKER_NOT_FOUND.value)
        self.assert_integrity(worker)
        max_c = worker.max_concurrent_tasks if max_concurrent_tasks is None else int(max_concurrent_tasks)
        active = int(active_tasks)
        if max_c < 1 or active < 0 or active > max_c:
            raise ValueError(WorkerRegistryBlock.CAPACITY_INVALID.value)
        updated = self._replace(worker, max_concurrent_tasks=max_c, active_tasks=active)
        if self.capacity_registry is not None:
            cap = self.capacity_registry.get(worker_id)
            if cap is None:
                self.capacity_registry.register(agent_id=worker_id, max_concurrent_tasks=max_c, active_tasks=active)
            else:
                self.capacity_registry.assert_integrity(cap)
                self.capacity_registry.update_load(worker_id, active_tasks=active, load_score=cap.load_score)
        return updated

    def pool_counts(self, pool_id: str) -> Mapping[str, int]:
        pool = self.get_pool(pool_id)
        if pool is None:
            raise ValueError(WorkerRegistryBlock.POOL_NOT_FOUND.value)
        self.assert_pool_integrity(pool)
        rows = self.list_workers()
        members = [w for w in rows if w.pool_id == pool_id]
        return {s.value: sum(1 for w in members if w.lifecycle == s) for s in WorkerLifecycle}

    def selection_snapshot(self) -> Mapping[str, Any]:
        workers = self.list_workers()
        return {
            "version": self.VERSION,
            "workspace_id": self.workspace_id,
            "worker_count": len(workers),
            "active_count": sum(w.lifecycle == WorkerLifecycle.ACTIVE for w in workers),
            "ready_count": sum(w.lifecycle == WorkerLifecycle.READY for w in workers),
            "draining_count": sum(w.lifecycle == WorkerLifecycle.DRAINING for w in workers),
            "healthy_count": sum(w.health == WorkerHealth.HEALTHY for w in workers),
            "degraded_count": sum(w.health == WorkerHealth.DEGRADED for w in workers),
            "unavailable_count": sum(w.health == WorkerHealth.UNAVAILABLE for w in workers),
            "authority_granted": False,
            "execution_requested": False,
        }


__all__ = [
    "WorkerRegistryBlock", "WorkerKind", "WorkerLifecycle", "WorkerHealth",
    "WorkerPool", "WorkerRecord", "WorkerRegistry",
]
