"""Phase 7.31 — Coordinated Worker Progress & Dependency Synchronization.

Projects existing worker progress and verification-intake evidence into the
multi-worker coordination boundary. This service observes progress, determines
which downstream tasks are eligible to be considered next, and records an
immutable coordination snapshot. It never converts worker progress into
business success, releases lifecycle state, authorizes work, executes work,
invokes providers, or invokes the Action Kernel.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib, json, sqlite3

from services.v71_41_coordinated_worker_assignment_lifecycle import (
    CoordinatedWorkerAssignmentLifecycle, CoordinatedTaskState,
)
from services.v71_12_worker_task_handoff import WorkerTaskHandoffRegistry, HandoffState
from services.v71_13_worker_task_progress import WorkerTaskProgressRegistry, ProgressState
from services.v71_14_worker_result_verification_intake import (
    WorkerResultVerificationIntakeRegistry, IntakeState,
)


class ProgressSyncBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    LIFECYCLE_MISSING = "lifecycle_missing"
    LIFECYCLE_INVALID = "lifecycle_invalid"
    DUPLICATE = "duplicate"
    SNAPSHOT_TAMPERED = "snapshot_tampered"
    HANDOFF_INVALID = "handoff_invalid"
    AUTHORITY_VIOLATION = "authority_violation"


@dataclass(frozen=True)
class CoordinatedTaskProgressView:
    task_id: str
    assignment_id: str
    worker_id: str
    lifecycle_state: str
    handoff_id: str = ""
    handoff_state: str = ""
    progress_id: str = ""
    progress_sequence: int = 0
    progress_state: str = ""
    progress_fingerprint: str = ""
    verification_intake_id: str = ""
    verification_intake_state: str = ""
    dependency_ready: bool = False
    evidence_available: bool = False

    def payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "assignment_id": self.assignment_id,
            "worker_id": self.worker_id, "lifecycle_state": self.lifecycle_state,
            "handoff_id": self.handoff_id, "handoff_state": self.handoff_state,
            "progress_id": self.progress_id, "progress_sequence": self.progress_sequence,
            "progress_state": self.progress_state,
            "progress_fingerprint": self.progress_fingerprint,
            "verification_intake_id": self.verification_intake_id,
            "verification_intake_state": self.verification_intake_state,
            "dependency_ready": self.dependency_ready,
            "evidence_available": self.evidence_available,
        }


@dataclass(frozen=True)
class CoordinatedProgressSnapshot:
    snapshot_id: str
    workspace_id: str
    coordination_lifecycle_id: str
    plan_id: str
    tasks: tuple[CoordinatedTaskProgressView, ...]
    eligible_next_tasks: tuple[str, ...]
    coordination_only: bool = True
    authorization_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False
    action_kernel_invoked: bool = False
    snapshot_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id, "workspace_id": self.workspace_id,
            "coordination_lifecycle_id": self.coordination_lifecycle_id,
            "plan_id": self.plan_id,
            "tasks": [t.payload() for t in self.tasks],
            "eligible_next_tasks": list(self.eligible_next_tasks),
            "coordination_only": self.coordination_only,
            "authorization_granted": self.authorization_granted,
            "execution_requested": self.execution_requested,
            "provider_called": self.provider_called,
            "action_kernel_invoked": self.action_kernel_invoked,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class CoordinatedWorkerProgressSynchronization:
    VERSION = "phase7.31"

    def __init__(self, workspace_id: str,
                 lifecycle: CoordinatedWorkerAssignmentLifecycle,
                 handoffs: WorkerTaskHandoffRegistry,
                 progress: WorkerTaskProgressRegistry,
                 intake: WorkerResultVerificationIntakeRegistry | None = None,
                 db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        deps = (lifecycle, handoffs, progress)
        if not ws or any(getattr(x, "workspace_id", None) != ws for x in deps):
            raise ValueError(ProgressSyncBlock.TENANT_MISMATCH.value)
        if intake is not None and intake.workspace_id != ws:
            raise ValueError(ProgressSyncBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.lifecycle = lifecycle
        self.handoffs = handoffs
        self.progress = progress
        self.intake = intake
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS coordinated_progress_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            UNIQUE(workspace_id, snapshot_id)
        )""")
        self._db.commit()

    def synchronize(self, *, snapshot_id: str,
                    coordination_lifecycle_id: str) -> CoordinatedProgressSnapshot:
        sid = str(snapshot_id or "").strip()
        if not sid:
            raise ValueError(ProgressSyncBlock.LIFECYCLE_MISSING.value)
        if self.get(sid) is not None:
            raise ValueError(ProgressSyncBlock.DUPLICATE.value)
        lifecycle = self.lifecycle.get(coordination_lifecycle_id)
        if lifecycle is None:
            raise ValueError(ProgressSyncBlock.LIFECYCLE_MISSING.value)
        self.lifecycle.assert_integrity(lifecycle)
        views = []
        state_map = {t.task_id: t for t in lifecycle.tasks}
        for task in lifecycle.tasks:
            handoff = self.handoffs.latest_for_assignment(task.assignment_id)
            handoff_id = handoff.handoff_id if handoff else ""
            handoff_state = handoff.state.value if handoff else ""
            if handoff is not None:
                self.handoffs.assert_integrity(handoff)
                if handoff.workspace_id != self.workspace_id or handoff.assignment_id != task.assignment_id:
                    raise ValueError(ProgressSyncBlock.HANDOFF_INVALID.value)
            latest = self.progress.latest_for_handoff(handoff.handoff_id) if handoff else None
            if latest is not None:
                self.progress.assert_integrity(latest)
            intake = None
            if self.intake is not None and handoff is not None:
                # A verification intake is created only after SUBMITTED; absence is valid.
                candidates = self.intake.latest_for_handoff(handoff.handoff_id) if hasattr(self.intake, "latest_for_handoff") else None
                intake = candidates
                if intake is not None:
                    self.intake.assert_integrity(intake)
            deps_released = all(
                state_map.get(dep) is not None and
                state_map[dep].state is CoordinatedTaskState.RELEASED
                for dep in task.depends_on
            )
            dependency_ready = task.state is CoordinatedTaskState.ASSIGNED and deps_released
            views.append(CoordinatedTaskProgressView(
                task.task_id, task.assignment_id, task.worker_id, task.state.value,
                handoff_id, handoff_state,
                latest.progress_id if latest else "",
                latest.sequence if latest else 0,
                latest.state.value if latest else "",
                latest.progress_fingerprint if latest else "",
                intake.intake_id if intake else "",
                intake.state.value if intake else "",
                dependency_ready,
                latest is not None and bool(latest.evidence),
            ))
        eligible = tuple(sorted(v.task_id for v in views if v.dependency_ready))
        record = CoordinatedProgressSnapshot(
            sid, self.workspace_id, lifecycle.coordination_lifecycle_id, lifecycle.plan_id,
            tuple(views), eligible)
        record = CoordinatedProgressSnapshot(**{**record.__dict__, "snapshot_fingerprint": record.fingerprint()})
        self._persist(record)
        return record

    def eligible_next_tasks(self, snapshot_id: str) -> tuple[str, ...]:
        item = self._require(snapshot_id)
        return item.eligible_next_tasks

    def get(self, snapshot_id: str) -> CoordinatedProgressSnapshot | None:
        row = self._db.execute(
            "SELECT payload,fingerprint FROM coordinated_progress_snapshots WHERE snapshot_id=? AND workspace_id=?",
            (str(snapshot_id), self.workspace_id)).fetchone()
        if not row:
            return None
        p = json.loads(row[0])
        p["tasks"] = tuple(CoordinatedTaskProgressView(**t) for t in p["tasks"])
        p["eligible_next_tasks"] = tuple(p.get("eligible_next_tasks", ()))
        p["snapshot_fingerprint"] = row[1]
        return CoordinatedProgressSnapshot(**p)

    def assert_integrity(self, snapshot: CoordinatedProgressSnapshot) -> None:
        if snapshot.workspace_id != self.workspace_id:
            raise ValueError(ProgressSyncBlock.TENANT_MISMATCH.value)
        if (not snapshot.coordination_only or snapshot.authorization_granted or
                snapshot.execution_requested or snapshot.provider_called or
                snapshot.action_kernel_invoked):
            raise ValueError(ProgressSyncBlock.AUTHORITY_VIOLATION.value)
        if snapshot.snapshot_fingerprint != snapshot.fingerprint():
            raise ValueError(ProgressSyncBlock.SNAPSHOT_TAMPERED.value)
        stored = self.get(snapshot.snapshot_id)
        if stored is None or stored.snapshot_fingerprint != snapshot.snapshot_fingerprint:
            raise ValueError(ProgressSyncBlock.SNAPSHOT_TAMPERED.value)
        lifecycle = self.lifecycle.get(snapshot.coordination_lifecycle_id)
        if lifecycle is None:
            raise ValueError(ProgressSyncBlock.LIFECYCLE_INVALID.value)
        self.lifecycle.assert_integrity(lifecycle)

    def context(self, snapshot_id: str) -> dict[str, Any]:
        item = self._require(snapshot_id)
        return {
            "snapshot_id": item.snapshot_id,
            "coordination_lifecycle_id": item.coordination_lifecycle_id,
            "plan_id": item.plan_id,
            "eligible_next_tasks": list(item.eligible_next_tasks),
            "task_count": len(item.tasks),
            "coordination_only": True,
            "authorization_granted": False,
            "execution_requested": False,
            "provider_called": False,
            "action_kernel_invoked": False,
        }

    def _require(self, snapshot_id: str) -> CoordinatedProgressSnapshot:
        item = self.get(snapshot_id)
        if item is None:
            raise ValueError(ProgressSyncBlock.LIFECYCLE_MISSING.value)
        self.assert_integrity(item)
        return item

    def _persist(self, item: CoordinatedProgressSnapshot) -> CoordinatedProgressSnapshot:
        self._db.execute(
            "INSERT INTO coordinated_progress_snapshots VALUES (?,?,?,?)",
            (item.snapshot_id, item.workspace_id,
             json.dumps(item.canonical_payload(), sort_keys=True), item.snapshot_fingerprint))
        self._db.commit()
        return item

    def close(self) -> None:
        self._db.close()


__all__ = [
    "ProgressSyncBlock", "CoordinatedTaskProgressView", "CoordinatedProgressSnapshot",
    "CoordinatedWorkerProgressSynchronization",
]
