"""Phase 7.33 — Coordinated Verification Outcome Propagation & Workforce Closure.

Consumes externally-authoritative verification dispositions and projects them into
workforce closure. This service never computes verification truth, rewrites a
verification record, or asserts a business outcome.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib, json, sqlite3

from services.v71_43_coordinated_result_verification_handoff import (
    CoordinatedWorkerResultVerificationHandoff, CoordinatedVerificationPackage,
    ResultAggregationState,
)
from services.v71_41_coordinated_worker_assignment_lifecycle import (
    CoordinatedWorkerAssignmentLifecycle, CoordinatedTaskState,
)
from services.v71_15_worker_outcome_feedback_closure import (
    WorkerOutcomeFeedbackClosureRegistry, VerifiedDisposition, ClosureState,
)


class CoordinatedClosureState(str, Enum):
    PROPAGATED = "propagated"
    CLOSED = "closed"
    ESCALATED = "escalated"


class OutcomePropagationBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    PACKAGE_MISSING = "package_missing"
    PACKAGE_INVALID = "package_invalid"
    PACKAGE_NOT_READY = "package_not_ready_for_verification"
    OUTCOME_MISSING = "verification_outcome_missing"
    OUTCOME_DUPLICATE = "verification_outcome_duplicate"
    OUTCOME_INVALID = "verification_outcome_invalid"
    INTAKE_MISMATCH = "verification_intake_mismatch"
    VERIFICATION_ID_REQUIRED = "verification_id_required"
    VERIFICATION_FINGERPRINT_REQUIRED = "verification_fingerprint_required"
    TASK_MISSING = "task_missing"
    LIFECYCLE_NOT_ACTIVE = "lifecycle_not_active"
    CLOSURE_FAILED = "closure_failed"
    DUPLICATE = "duplicate"
    TAMPERED = "closure_tampered"
    AUTHORITY_VIOLATION = "authority_violation"


@dataclass(frozen=True)
class CanonicalVerificationOutcome:
    task_id: str
    intake_id: str
    verification_id: str
    verification_fingerprint: str
    disposition: VerifiedDisposition
    reason: str = "verified_disposition_received"

    def payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "intake_id": self.intake_id,
            "verification_id": self.verification_id,
            "verification_fingerprint": self.verification_fingerprint,
            "disposition": self.disposition.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CoordinatedWorkforceClosure:
    closure_id: str
    workspace_id: str
    package_id: str
    coordination_lifecycle_id: str
    plan_id: str
    state: CoordinatedClosureState
    outcomes: tuple[CanonicalVerificationOutcome, ...]
    worker_closure_ids: tuple[str, ...]
    disposition_counts: dict[str, int]
    coordination_only: bool = True
    authorization_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False
    action_kernel_invoked: bool = False
    business_outcome_asserted: bool = False
    closure_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "closure_id": self.closure_id,
            "workspace_id": self.workspace_id,
            "package_id": self.package_id,
            "coordination_lifecycle_id": self.coordination_lifecycle_id,
            "plan_id": self.plan_id,
            "state": self.state.value,
            "outcomes": [x.payload() for x in self.outcomes],
            "worker_closure_ids": list(self.worker_closure_ids),
            "disposition_counts": dict(sorted(self.disposition_counts.items())),
            "coordination_only": self.coordination_only,
            "authorization_granted": self.authorization_granted,
            "execution_requested": self.execution_requested,
            "provider_called": self.provider_called,
            "action_kernel_invoked": self.action_kernel_invoked,
            "business_outcome_asserted": self.business_outcome_asserted,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode()).hexdigest()


class CoordinatedVerificationOutcomeClosure:
    VERSION = "phase7.33"

    def __init__(self, workspace_id: str,
                 package_registry: CoordinatedWorkerResultVerificationHandoff,
                 lifecycle: CoordinatedWorkerAssignmentLifecycle,
                 worker_closure: WorkerOutcomeFeedbackClosureRegistry,
                 db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or any(x.workspace_id != ws for x in (package_registry, lifecycle, worker_closure)):
            raise ValueError(OutcomePropagationBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.package_registry = package_registry
        self.lifecycle = lifecycle
        self.worker_closure = worker_closure
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS coordinated_workforce_closure (
            closure_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            package_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            UNIQUE(workspace_id, closure_id),
            UNIQUE(workspace_id, package_id)
        )""")
        self._db.commit()

    def close_from_verification(self, *, closure_id: str, package_id: str,
                                outcomes: tuple[CanonicalVerificationOutcome, ...]) -> CoordinatedWorkforceClosure:
        cid = str(closure_id or "").strip()
        pid = str(package_id or "").strip()
        if not cid or not pid:
            raise ValueError(OutcomePropagationBlock.PACKAGE_MISSING.value)
        if self.get(cid) is not None or self._db.execute(
            "SELECT closure_id FROM coordinated_workforce_closure WHERE package_id=? AND workspace_id=?",
            (pid, self.workspace_id)).fetchone():
            raise ValueError(OutcomePropagationBlock.DUPLICATE.value)

        package = self.package_registry.get(pid)
        if package is None:
            raise ValueError(OutcomePropagationBlock.PACKAGE_MISSING.value)
        self.package_registry.assert_integrity(package)
        # A PARTIAL package is valid for incremental propagation: an upstream
        # task may be verifiable before downstream tasks are even eligible.
        # READY_FOR_VERIFICATION means the package contains the complete current
        # evidence set, but does not require the workforce objective to be closed.
        if package.state not in {ResultAggregationState.READY_FOR_VERIFICATION, ResultAggregationState.PARTIAL}:
            raise ValueError(OutcomePropagationBlock.PACKAGE_NOT_READY.value)

        evidence = {x.task_id: x for x in package.results}
        provided = tuple(outcomes)
        if not provided or not set(x.task_id for x in provided).issubset(set(evidence)):
            raise ValueError(OutcomePropagationBlock.OUTCOME_MISSING.value)
        if len({x.task_id for x in provided}) != len(provided):
            raise ValueError(OutcomePropagationBlock.OUTCOME_DUPLICATE.value)

        normalized = []
        for outcome in sorted(provided, key=lambda x: x.task_id):
            ev = evidence.get(outcome.task_id)
            if ev is None or ev.intake_id != outcome.intake_id:
                raise ValueError(OutcomePropagationBlock.INTAKE_MISMATCH.value)
            if not str(outcome.verification_id or "").strip():
                raise ValueError(OutcomePropagationBlock.VERIFICATION_ID_REQUIRED.value)
            if not str(outcome.verification_fingerprint or "").strip():
                raise ValueError(OutcomePropagationBlock.VERIFICATION_FINGERPRINT_REQUIRED.value)
            try:
                disposition = outcome.disposition if isinstance(outcome.disposition, VerifiedDisposition) else VerifiedDisposition(str(outcome.disposition))
            except ValueError as exc:
                raise ValueError(OutcomePropagationBlock.OUTCOME_INVALID.value) from exc
            normalized.append(CanonicalVerificationOutcome(
                outcome.task_id, outcome.intake_id, outcome.verification_id,
                outcome.verification_fingerprint, disposition,
                str(outcome.reason or "verified_disposition_received").strip() or "verified_disposition_received",
            ))

        record = self.lifecycle.get(package.coordination_lifecycle_id)
        if record is None:
            raise ValueError(OutcomePropagationBlock.PACKAGE_INVALID.value)
        self.lifecycle.assert_integrity(record)

        worker_closure_ids = []
        counts = {d.value: 0 for d in VerifiedDisposition}
        # Propagate through the canonical per-assignment closure boundary first.
        # It owns logical capacity release; this coordinator only records the projection.
        for outcome in normalized:
            ev = evidence[outcome.task_id]
            task = next((t for t in record.tasks if t.task_id == outcome.task_id), None)
            if task is None:
                raise ValueError(OutcomePropagationBlock.TASK_MISSING.value)
            current = self.lifecycle.lifecycle.latest_for_assignment(task.assignment_id)
            if current is None:
                raise ValueError(OutcomePropagationBlock.LIFECYCLE_NOT_ACTIVE.value)
            self.lifecycle.lifecycle.assert_integrity(current)
            existing = self.worker_closure.latest_for_assignment(task.assignment_id)
            if existing is not None:
                self.worker_closure.assert_integrity(existing)
                if existing.verification_id != outcome.verification_id or existing.verification_fingerprint != outcome.verification_fingerprint:
                    raise ValueError(OutcomePropagationBlock.OUTCOME_INVALID.value)
                worker_closure_ids.append(existing.closure_id)
            else:
                if task.state is not CoordinatedTaskState.ACTIVE or current.state.value != "active":
                    raise ValueError(OutcomePropagationBlock.LIFECYCLE_NOT_ACTIVE.value)
                closed = self.worker_closure.close_from_verification(
                    closure_id=f"{cid}:{task.task_id}", intake_id=outcome.intake_id,
                    verification_id=outcome.verification_id,
                    verification_fingerprint=outcome.verification_fingerprint,
                    disposition=outcome.disposition, reason=outcome.reason)
                worker_closure_ids.append(closed.closure_id)
            counts[outcome.disposition.value] += 1

        # Only after every per-worker closure succeeded do we project terminal release
        # into the coordinated lifecycle. No canonical business outcome is written.
        current = self.lifecycle.get(package.coordination_lifecycle_id)
        for outcome in normalized:
            task = next(t for t in current.tasks if t.task_id == outcome.task_id)
            latest = self.lifecycle.lifecycle.latest_for_assignment(task.assignment_id)
            if latest is None or latest.state.value not in {"released", "cancelled"}:
                raise ValueError(OutcomePropagationBlock.CLOSURE_FAILED.value)
            if task.state is CoordinatedTaskState.ACTIVE:
                current = self.lifecycle.mark_task_terminal_from_canonical_closure(
                    coordination_lifecycle_id=current.coordination_lifecycle_id,
                    task_id=task.task_id,
                    terminal_state=CoordinatedTaskState.RELEASED if latest.state.value == "released" else CoordinatedTaskState.CANCELLED,
                )

        latest_record = self.lifecycle.get(package.coordination_lifecycle_id)
        if latest_record is None:
            raise ValueError(OutcomePropagationBlock.PACKAGE_INVALID.value)
        all_terminal = all(t.state in {CoordinatedTaskState.RELEASED, CoordinatedTaskState.CANCELLED}
                           for t in latest_record.tasks)
        prior_closures = []
        for task in latest_record.tasks:
            prior = self.worker_closure.latest_for_assignment(task.assignment_id)
            if prior is not None:
                self.worker_closure.assert_integrity(prior)
                prior_closures.append(prior)
        escalated = any(o.disposition in (VerifiedDisposition.VERIFIED_UNCERTAIN, VerifiedDisposition.VERIFIED_REJECTED) for o in normalized)
        escalated = escalated or any(x.disposition in (VerifiedDisposition.VERIFIED_UNCERTAIN, VerifiedDisposition.VERIFIED_REJECTED) for x in prior_closures)
        state = (CoordinatedClosureState.ESCALATED if escalated and all_terminal else
                 CoordinatedClosureState.CLOSED if all_terminal else
                 CoordinatedClosureState.ESCALATED if escalated else
                 CoordinatedClosureState.PROPAGATED)
        result = CoordinatedWorkforceClosure(
            cid, self.workspace_id, package.package_id, package.coordination_lifecycle_id,
            package.plan_id, state, tuple(normalized), tuple(worker_closure_ids), counts,
        )
        result = CoordinatedWorkforceClosure(**{**result.__dict__, "closure_fingerprint": result.fingerprint()})
        self._persist(result)
        return result

    def get(self, closure_id: str) -> CoordinatedWorkforceClosure | None:
        row = self._db.execute("SELECT payload,fingerprint FROM coordinated_workforce_closure WHERE closure_id=? AND workspace_id=?", (str(closure_id), self.workspace_id)).fetchone()
        if not row:
            return None
        p = json.loads(row[0])
        p["state"] = CoordinatedClosureState(p["state"])
        p["outcomes"] = tuple(CanonicalVerificationOutcome(
            x["task_id"], x["intake_id"], x["verification_id"], x["verification_fingerprint"], VerifiedDisposition(x["disposition"]), x.get("reason", "verified_disposition_received")) for x in p.get("outcomes", ()))
        p["worker_closure_ids"] = tuple(p.get("worker_closure_ids", ()))
        p["disposition_counts"] = dict(p.get("disposition_counts", {}))
        p["closure_fingerprint"] = row[1]
        return CoordinatedWorkforceClosure(**p)

    def assert_integrity(self, record: CoordinatedWorkforceClosure) -> None:
        if record.workspace_id != self.workspace_id:
            raise ValueError(OutcomePropagationBlock.TENANT_MISMATCH.value)
        if (not record.coordination_only or record.authorization_granted or record.execution_requested or
                record.provider_called or record.action_kernel_invoked or record.business_outcome_asserted):
            raise ValueError(OutcomePropagationBlock.AUTHORITY_VIOLATION.value)
        if record.closure_fingerprint != record.fingerprint():
            raise ValueError(OutcomePropagationBlock.TAMPERED.value)
        stored = self.get(record.closure_id)
        if stored is None or stored.closure_fingerprint != record.closure_fingerprint:
            raise ValueError(OutcomePropagationBlock.TAMPERED.value)
        package = self.package_registry.get(record.package_id)
        if package is None:
            raise ValueError(OutcomePropagationBlock.PACKAGE_MISSING.value)
        self.package_registry.assert_integrity(package)
        if package.coordination_lifecycle_id != record.coordination_lifecycle_id or package.plan_id != record.plan_id:
            raise ValueError(OutcomePropagationBlock.TAMPERED.value)

    def context(self, closure_id: str) -> dict[str, Any]:
        record = self.get(closure_id)
        if record is None:
            raise ValueError(OutcomePropagationBlock.PACKAGE_MISSING.value)
        self.assert_integrity(record)
        return {
            "closure_id": record.closure_id,
            "package_id": record.package_id,
            "coordination_lifecycle_id": record.coordination_lifecycle_id,
            "state": record.state.value,
            "result_count": len(record.outcomes),
            "disposition_counts": dict(record.disposition_counts),
            "objective_closed": self.lifecycle.get(record.coordination_lifecycle_id) is not None and all(
                t.state in {CoordinatedTaskState.RELEASED, CoordinatedTaskState.CANCELLED}
                for t in self.lifecycle.get(record.coordination_lifecycle_id).tasks),
            "coordination_only": True,
            "authorization_granted": False,
            "execution_requested": False,
            "provider_called": False,
            "action_kernel_invoked": False,
            "business_outcome_asserted": False,
        }

    def _persist(self, record: CoordinatedWorkforceClosure) -> None:
        self._db.execute("INSERT INTO coordinated_workforce_closure VALUES (?,?,?,?,?)", (
            record.closure_id, record.workspace_id, record.package_id,
            json.dumps(record.canonical_payload(), sort_keys=True), record.closure_fingerprint))
        self._db.commit()

    def close(self) -> None:
        self._db.close()


__all__ = [
    "CoordinatedClosureState", "OutcomePropagationBlock",
    "CanonicalVerificationOutcome", "CoordinatedWorkforceClosure",
    "CoordinatedVerificationOutcomeClosure",
]
