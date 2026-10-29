"""V71.24 — Governed Workforce Capacity Allocation.

Turns an approved/advisory workforce demand plan into a bounded, auditable logical
capacity reservation for scheduling. Allocation is not worker assignment,
provisioning, authorization, or execution.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib, json, sqlite3

from services.v71_21_worker_registry import WorkerRegistry, WorkerLifecycle
from services.v71_23_workforce_demand_planning import WorkforceDemandPlan, DemandDisposition


class AllocationDisposition(str, Enum):
    HOLD = "hold"
    RESERVE = "reserve"
    STABLE = "stable"
    RELEASE = "release"


class AllocationBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    PLAN_INVALID = "plan_invalid"
    CAPACITY_INVALID = "capacity_invalid"
    SAFETY_HOLD = "safety_hold"
    ALLOCATION_TAMPERED = "allocation_tampered"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class CapacityReservation:
    reservation_id: str
    workspace_id: str
    pool_id: str
    plan_id: str
    plan_fingerprint: str
    requested_slots: int
    reserved_slots: int
    available_slots: int
    disposition: AllocationDisposition
    reason_codes: tuple[str, ...]
    advisory_only: bool = True
    provisioning_authority_granted: bool = False
    assignment_authority_granted: bool = False
    execution_requested: bool = False
    reservation_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "workspace_id": self.workspace_id,
            "pool_id": self.pool_id,
            "plan_id": self.plan_id,
            "plan_fingerprint": self.plan_fingerprint,
            "requested_slots": self.requested_slots,
            "reserved_slots": self.reserved_slots,
            "available_slots": self.available_slots,
            "disposition": self.disposition.value,
            "reason_codes": list(self.reason_codes),
            "advisory_only": self.advisory_only,
            "provisioning_authority_granted": self.provisioning_authority_granted,
            "assignment_authority_granted": self.assignment_authority_granted,
            "execution_requested": self.execution_requested,
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class WorkforceCapacityAllocation:
    VERSION = "v71.24"

    def __init__(self, workspace_id: str, worker_registry: WorkerRegistry, db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or worker_registry.workspace_id != ws:
            raise ValueError(AllocationBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.worker_registry = worker_registry
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS workforce_capacity_reservations (
            reservation_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL
        )""")
        self._db.commit()

    def allocate(self, *, reservation_id: str, plan: WorkforceDemandPlan, requested_slots: int,
                 safety_hold: bool = False) -> CapacityReservation:
        if plan.workspace_id != self.workspace_id:
            raise ValueError(AllocationBlock.TENANT_MISMATCH.value)
        if plan.plan_fingerprint != plan.fingerprint() or not plan.advisory_only or plan.provisioning_authority_granted or plan.execution_requested:
            raise ValueError(AllocationBlock.PLAN_INVALID.value)
        if reservation_id in self._ids():
            raise ValueError(AllocationBlock.DUPLICATE.value)
        if requested_slots < 0:
            raise ValueError(AllocationBlock.CAPACITY_INVALID.value)
        pool = self.worker_registry.get_pool(plan.pool_id)
        if pool is None:
            raise ValueError(AllocationBlock.CAPACITY_INVALID.value)
        self.worker_registry.assert_pool_integrity(pool)
        counts = self.worker_registry.pool_counts(pool.pool_id)
        active_workers = counts.get("active", 0)
        active = self.worker_registry.list_workers(lifecycle=WorkerLifecycle.ACTIVE)
        members = tuple(w for w in active if w.pool_id == pool.pool_id)
        available = sum(w.max_concurrent_tasks for w in members)

        if safety_hold or plan.disposition == DemandDisposition.HOLD:
            disposition, reserved, reasons = AllocationDisposition.HOLD, 0, ("safety_hold" if safety_hold else "demand_plan_hold",)
        elif requested_slots == 0:
            disposition, reserved, reasons = AllocationDisposition.STABLE, 0, ("no_capacity_requested",)
        elif requested_slots <= available:
            disposition, reserved, reasons = AllocationDisposition.RESERVE, requested_slots, ("capacity_available",)
        else:
            disposition, reserved, reasons = AllocationDisposition.HOLD, 0, ("capacity_insufficient",)

        record = CapacityReservation(reservation_id, self.workspace_id, pool.pool_id, plan.plan_id,
                                     plan.plan_fingerprint, requested_slots, reserved, available,
                                     disposition, reasons, True, False, False, False, "")
        record = CapacityReservation(**{**record.__dict__, "reservation_fingerprint": record.fingerprint()})
        self._db.execute("INSERT INTO workforce_capacity_reservations VALUES (?,?,?,?)",
                          (record.reservation_id, record.workspace_id, json.dumps(record.canonical_payload(), sort_keys=True), record.reservation_fingerprint))
        self._db.commit()
        return record

    def _ids(self) -> set[str]:
        return {r[0] for r in self._db.execute("SELECT reservation_id FROM workforce_capacity_reservations")}

    def get(self, reservation_id: str) -> CapacityReservation | None:
        row = self._db.execute("SELECT payload FROM workforce_capacity_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if not row:
            return None
        p = json.loads(row[0])
        p["reason_codes"] = tuple(p.get("reason_codes", ()))
        p["disposition"] = AllocationDisposition(p["disposition"])
        p["reservation_fingerprint"] = row[0] and self._db.execute(
            "SELECT fingerprint FROM workforce_capacity_reservations WHERE reservation_id=?", (reservation_id,)
        ).fetchone()[0]
        return CapacityReservation(**p)

    def assert_integrity(self, reservation: CapacityReservation) -> None:
        if reservation.workspace_id != self.workspace_id:
            raise ValueError(AllocationBlock.TENANT_MISMATCH.value)
        if not reservation.advisory_only or reservation.provisioning_authority_granted or reservation.assignment_authority_granted or reservation.execution_requested:
            raise ValueError(AllocationBlock.ALLOCATION_TAMPERED.value)
        if reservation.reserved_slots < 0 or reservation.reserved_slots > reservation.available_slots or reservation.reserved_slots > reservation.requested_slots:
            raise ValueError(AllocationBlock.ALLOCATION_TAMPERED.value)
        if reservation.reservation_fingerprint != reservation.fingerprint():
            raise ValueError(AllocationBlock.ALLOCATION_TAMPERED.value)
        stored = self.get(reservation.reservation_id)
        if stored is None or stored.reservation_fingerprint != reservation.reservation_fingerprint:
            raise ValueError(AllocationBlock.ALLOCATION_TAMPERED.value)

    def close(self) -> None:
        self._db.close()
