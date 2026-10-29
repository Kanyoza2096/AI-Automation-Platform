"""Phase 7.32 — Coordinated Worker Result Aggregation & Verification Handoff.

Aggregates per-worker verification-intake records into one durable verification
package for a coordinated assignment set. The package is evidence/provenance
only: it never verifies results, declares business success/failure, authorizes,
executes, calls providers, or invokes the Action Kernel.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib, json, sqlite3

from services.v71_41_coordinated_worker_assignment_lifecycle import (
    CoordinatedWorkerAssignmentLifecycle,
    CoordinatedTaskState,
)
from services.v71_42_coordinated_worker_progress_synchronization import (
    CoordinatedWorkerProgressSynchronization,
)
from services.v71_14_worker_result_verification_intake import (
    WorkerResultVerificationIntakeRegistry, IntakeState,
)


class ResultAggregationState(str, Enum):
    PARTIAL = "partial"
    READY_FOR_VERIFICATION = "ready_for_verification"
    BLOCKED = "blocked"


class ResultAggregationBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    LIFECYCLE_MISSING = "lifecycle_missing"
    SNAPSHOT_MISSING = "snapshot_missing"
    SNAPSHOT_INVALID = "snapshot_invalid"
    INTAKE_MISSING = "intake_missing"
    INTAKE_INVALID = "intake_invalid"
    INTAKE_NOT_HANDED = "intake_not_handed_to_verification"
    DUPLICATE = "duplicate"
    PACKAGE_TAMPERED = "package_tampered"
    AUTHORITY_VIOLATION = "authority_violation"


@dataclass(frozen=True)
class CoordinatedResultEvidence:
    task_id: str
    assignment_id: str
    worker_id: str
    intake_id: str
    intake_state: str
    submitted_progress_id: str
    submitted_progress_fingerprint: str
    evidence_manifest: dict[str, Any]

    def payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "assignment_id": self.assignment_id,
            "worker_id": self.worker_id,
            "intake_id": self.intake_id,
            "intake_state": self.intake_state,
            "submitted_progress_id": self.submitted_progress_id,
            "submitted_progress_fingerprint": self.submitted_progress_fingerprint,
            "evidence_manifest": self.evidence_manifest,
        }


@dataclass(frozen=True)
class CoordinatedVerificationPackage:
    package_id: str
    workspace_id: str
    coordination_lifecycle_id: str
    plan_id: str
    snapshot_id: str
    state: ResultAggregationState
    results: tuple[CoordinatedResultEvidence, ...]
    missing_task_ids: tuple[str, ...]
    coordination_only: bool = True
    verification_requested: bool = True
    authorization_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False
    action_kernel_invoked: bool = False
    business_outcome_asserted: bool = False
    package_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "workspace_id": self.workspace_id,
            "coordination_lifecycle_id": self.coordination_lifecycle_id,
            "plan_id": self.plan_id,
            "snapshot_id": self.snapshot_id,
            "state": self.state.value,
            "results": [x.payload() for x in self.results],
            "missing_task_ids": list(self.missing_task_ids),
            "coordination_only": self.coordination_only,
            "verification_requested": self.verification_requested,
            "authorization_granted": self.authorization_granted,
            "execution_requested": self.execution_requested,
            "provider_called": self.provider_called,
            "action_kernel_invoked": self.action_kernel_invoked,
            "business_outcome_asserted": self.business_outcome_asserted,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode()).hexdigest()


class CoordinatedWorkerResultVerificationHandoff:
    VERSION = "phase7.32"

    def __init__(self, workspace_id: str,
                 lifecycle: CoordinatedWorkerAssignmentLifecycle,
                 synchronization: CoordinatedWorkerProgressSynchronization,
                 intake: WorkerResultVerificationIntakeRegistry,
                 db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or any(x.workspace_id != ws for x in (lifecycle, synchronization, intake)):
            raise ValueError(ResultAggregationBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.lifecycle = lifecycle
        self.synchronization = synchronization
        self.intake = intake
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS coordinated_verification_packages (
            package_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            UNIQUE(workspace_id, package_id)
        )""")
        self._db.commit()

    def aggregate(self, *, package_id: str, coordination_lifecycle_id: str,
                  snapshot_id: str) -> CoordinatedVerificationPackage:
        pid = str(package_id or "").strip()
        if not pid:
            raise ValueError(ResultAggregationBlock.LIFECYCLE_MISSING.value)
        if self.get(pid) is not None:
            raise ValueError(ResultAggregationBlock.DUPLICATE.value)

        lifecycle = self.lifecycle.get(coordination_lifecycle_id)
        if lifecycle is None:
            raise ValueError(ResultAggregationBlock.LIFECYCLE_MISSING.value)
        self.lifecycle.assert_integrity(lifecycle)

        snapshot = self.synchronization.get(snapshot_id)
        if snapshot is None:
            raise ValueError(ResultAggregationBlock.SNAPSHOT_MISSING.value)
        self.synchronization.assert_integrity(snapshot)
        if snapshot.coordination_lifecycle_id != coordination_lifecycle_id:
            raise ValueError(ResultAggregationBlock.SNAPSHOT_INVALID.value)

        results = []
        missing = []
        for task in lifecycle.tasks:
            view = next((x for x in snapshot.tasks if x.task_id == task.task_id), None)
            if view is None:
                raise ValueError(ResultAggregationBlock.SNAPSHOT_INVALID.value)
            if not view.handoff_id:
                missing.append(task.task_id)
                continue
            intake = self.intake.latest_for_handoff(view.handoff_id)
            if intake is None:
                missing.append(task.task_id)
                continue
            try:
                self.intake.assert_integrity(intake)
            except ValueError as exc:
                raise ValueError(ResultAggregationBlock.INTAKE_INVALID.value) from exc
            if intake.state is not IntakeState.HANDED_TO_VERIFICATION:
                missing.append(task.task_id)
                continue
            results.append(CoordinatedResultEvidence(
                task.task_id, task.assignment_id, task.worker_id,
                intake.intake_id, intake.state.value,
                intake.submitted_progress_id,
                intake.submitted_progress_fingerprint,
                intake.evidence_manifest,
            ))

        missing_tuple = tuple(sorted(missing))
        state = (ResultAggregationState.READY_FOR_VERIFICATION
                 if not missing_tuple else ResultAggregationState.PARTIAL)
        package = CoordinatedVerificationPackage(
            pid, self.workspace_id, lifecycle.coordination_lifecycle_id,
            lifecycle.plan_id, snapshot_id, state, tuple(sorted(results, key=lambda x: x.task_id)),
            missing_tuple,
        )
        package = CoordinatedVerificationPackage(**{
            **package.__dict__, "package_fingerprint": package.fingerprint()
        })
        self._persist(package)
        return package

    def get(self, package_id: str) -> CoordinatedVerificationPackage | None:
        row = self._db.execute(
            "SELECT payload,fingerprint FROM coordinated_verification_packages "
            "WHERE package_id=? AND workspace_id=?", (str(package_id), self.workspace_id)
        ).fetchone()
        if not row:
            return None
        p = json.loads(row[0])
        p["state"] = ResultAggregationState(p["state"])
        p["results"] = tuple(CoordinatedResultEvidence(**x) for x in p.get("results", ()))
        p["missing_task_ids"] = tuple(p.get("missing_task_ids", ()))
        p["package_fingerprint"] = row[1]
        return CoordinatedVerificationPackage(**p)

    def assert_integrity(self, package: CoordinatedVerificationPackage) -> None:
        if package.workspace_id != self.workspace_id:
            raise ValueError(ResultAggregationBlock.TENANT_MISMATCH.value)
        if (not package.coordination_only or not package.verification_requested or
                package.authorization_granted or package.execution_requested or
                package.provider_called or package.action_kernel_invoked or
                package.business_outcome_asserted):
            raise ValueError(ResultAggregationBlock.AUTHORITY_VIOLATION.value)
        if package.package_fingerprint != package.fingerprint():
            raise ValueError(ResultAggregationBlock.PACKAGE_TAMPERED.value)
        stored = self.get(package.package_id)
        if stored is None or stored.package_fingerprint != package.package_fingerprint:
            raise ValueError(ResultAggregationBlock.PACKAGE_TAMPERED.value)
        lifecycle = self.lifecycle.get(package.coordination_lifecycle_id)
        if lifecycle is None:
            raise ValueError(ResultAggregationBlock.LIFECYCLE_MISSING.value)
        self.lifecycle.assert_integrity(lifecycle)
        snapshot = self.synchronization.get(package.snapshot_id)
        if snapshot is None or snapshot.coordination_lifecycle_id != package.coordination_lifecycle_id:
            raise ValueError(ResultAggregationBlock.SNAPSHOT_INVALID.value)
        self.synchronization.assert_integrity(snapshot)
        if package.state is ResultAggregationState.READY_FOR_VERIFICATION and package.missing_task_ids:
            raise ValueError(ResultAggregationBlock.PACKAGE_TAMPERED.value)

    def context(self, package_id: str) -> dict[str, Any]:
        package = self.get(package_id)
        if package is None:
            raise ValueError(ResultAggregationBlock.LIFECYCLE_MISSING.value)
        self.assert_integrity(package)
        return {
            "package_id": package.package_id,
            "coordination_lifecycle_id": package.coordination_lifecycle_id,
            "plan_id": package.plan_id,
            "snapshot_id": package.snapshot_id,
            "state": package.state.value,
            "result_count": len(package.results),
            "missing_task_ids": list(package.missing_task_ids),
            "coordination_only": True,
            "verification_requested": True,
            "authorization_granted": False,
            "execution_requested": False,
            "provider_called": False,
            "action_kernel_invoked": False,
            "business_outcome_asserted": False,
        }

    def _persist(self, package: CoordinatedVerificationPackage) -> None:
        self._db.execute(
            "INSERT INTO coordinated_verification_packages VALUES (?,?,?,?)",
            (package.package_id, package.workspace_id,
             json.dumps(package.canonical_payload(), sort_keys=True),
             package.package_fingerprint),
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()


__all__ = [
    "ResultAggregationState", "ResultAggregationBlock",
    "CoordinatedResultEvidence", "CoordinatedVerificationPackage",
    "CoordinatedWorkerResultVerificationHandoff",
]
