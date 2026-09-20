"""C8.2 database failure/transaction certification.

Deterministic pre-production harness for failure boundaries around durable state.
It deliberately models transaction commit visibility separately from external side
 effects: a crash at any boundary must never manufacture a successful receipt or
 silently authorize a duplicate retry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
import hashlib, json


class FailurePoint(str, Enum):
    BEFORE_TX = "before_transaction"
    AFTER_STATE_BEFORE_COMMIT = "after_state_before_commit"
    AFTER_COMMIT_BEFORE_RECEIPT = "after_commit_before_receipt"
    AFTER_SIDE_EFFECT_BEFORE_COMMIT = "after_side_effect_before_commit"
    RECEIPT_PERSISTENCE_FAILURE = "receipt_persistence_failure"
    STALE_LEASE = "stale_lease"


@dataclass(frozen=True)
class FailureResult:
    point: str
    passed: bool
    invariant: str
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DatabaseFailureReport:
    version: str
    scenarios: tuple[FailureResult, ...]
    passed: int
    failed: int
    certification: str
    fingerprint: str


@dataclass
class _Execution:
    intent_id: str
    state: str = "pending"
    committed: bool = False
    side_effect: bool = False
    receipt: bool = False
    uncertain: bool = False
    lease_owner: str | None = None


class DatabaseFailureSimulator:
    """Small state machine used to certify invariants without pretending it is PostgreSQL."""

    def __init__(self) -> None:
        self.executions: dict[str, _Execution] = {}

    def create(self, intent_id: str = "intent-1") -> _Execution:
        row = _Execution(intent_id)
        self.executions[intent_id] = row
        return row

    def claim(self, row: _Execution, owner: str) -> bool:
        if row.lease_owner is not None or row.state not in {"pending", "retryable"}:
            return False
        row.lease_owner = owner
        row.state = "processing"
        return True

    def crash(self, row: _Execution, point: FailurePoint) -> None:
        if point in {FailurePoint.AFTER_STATE_BEFORE_COMMIT, FailurePoint.BEFORE_TX}:
            # Uncommitted state is invisible after a real transaction rollback.
            row.committed = False
            row.state = "pending"
            row.lease_owner = None
            return
        if point == FailurePoint.AFTER_COMMIT_BEFORE_RECEIPT:
            row.committed = True
            row.state = "uncertain"
            row.uncertain = True
            row.lease_owner = None
            return
        if point == FailurePoint.AFTER_SIDE_EFFECT_BEFORE_COMMIT:
            row.side_effect = True
            row.committed = False
            row.state = "uncertain"
            row.uncertain = True
            row.lease_owner = None
            return
        if point == FailurePoint.RECEIPT_PERSISTENCE_FAILURE:
            row.committed = True
            row.side_effect = True
            row.receipt = False
            row.state = "uncertain"
            row.uncertain = True
            row.lease_owner = None
            return
        if point == FailurePoint.STALE_LEASE:
            row.state = "uncertain"
            row.uncertain = True
            row.lease_owner = None

    def blind_retry(self, row: _Execution, owner: str) -> bool:
        # Uncertainty must fence the external side effect from an implicit retry.
        return self.claim(row, owner)


class DatabaseFailureCertification:
    VERSION = "c8.2"

    def __init__(self) -> None:
        self.sim = DatabaseFailureSimulator()

    def _result(self, point: FailurePoint, passed: bool, invariant: str, **evidence: Any) -> FailureResult:
        return FailureResult(point.value, passed, invariant, evidence)

    def run(self) -> DatabaseFailureReport:
        results = [
            self._before_tx(), self._rollback_before_commit(),
            self._commit_without_receipt(), self._side_effect_before_commit(),
            self._receipt_persistence_failure(), self._stale_lease(),
        ]
        passed = sum(x.passed for x in results)
        failed = len(results) - passed
        canonical = [{"point": x.point, "passed": x.passed, "invariant": x.invariant, "evidence": dict(x.evidence)} for x in results]
        fp = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return DatabaseFailureReport(self.VERSION, tuple(results), passed, failed,
                                     "PASS" if failed == 0 else "NOT_PROVEN", fp)

    def _before_tx(self) -> FailureResult:
        row = self.sim.create("before-tx")
        claimed = self.sim.claim(row, "worker-a")
        self.sim.crash(row, FailurePoint.BEFORE_TX)
        return self._result(FailurePoint.BEFORE_TX, claimed and row.state == "pending" and not row.committed,
                            "a crash before transaction work leaves no committed execution state", state=row.state)

    def _rollback_before_commit(self) -> FailureResult:
        row = self.sim.create("rollback")
        self.sim.claim(row, "worker-a")
        self.sim.crash(row, FailurePoint.AFTER_STATE_BEFORE_COMMIT)
        return self._result(FailurePoint.AFTER_STATE_BEFORE_COMMIT, row.state == "pending" and not row.committed and not row.side_effect,
                            "uncommitted durable state is rolled back and cannot become a phantom success", state=row.state)

    def _commit_without_receipt(self) -> FailureResult:
        row = self.sim.create("commit-receipt")
        self.sim.claim(row, "worker-a")
        self.sim.crash(row, FailurePoint.AFTER_COMMIT_BEFORE_RECEIPT)
        retry = self.sim.blind_retry(row, "worker-b")
        return self._result(FailurePoint.AFTER_COMMIT_BEFORE_RECEIPT,
                            row.committed and row.uncertain and not retry,
                            "committed work without a receipt becomes uncertain and cannot be blindly replayed",
                            committed=row.committed, retry_allowed=retry)

    def _side_effect_before_commit(self) -> FailureResult:
        row = self.sim.create("side-effect")
        self.sim.claim(row, "worker-a")
        self.sim.crash(row, FailurePoint.AFTER_SIDE_EFFECT_BEFORE_COMMIT)
        retry = self.sim.blind_retry(row, "worker-b")
        return self._result(FailurePoint.AFTER_SIDE_EFFECT_BEFORE_COMMIT,
                            row.side_effect and row.uncertain and not retry,
                            "an external side effect before durable commit is fenced as uncertain",
                            side_effect=row.side_effect, retry_allowed=retry)

    def _receipt_persistence_failure(self) -> FailureResult:
        row = self.sim.create("receipt-failure")
        self.sim.claim(row, "worker-a")
        self.sim.crash(row, FailurePoint.RECEIPT_PERSISTENCE_FAILURE)
        retry = self.sim.blind_retry(row, "worker-b")
        return self._result(FailurePoint.RECEIPT_PERSISTENCE_FAILURE,
                            row.side_effect and row.committed and row.uncertain and not retry,
                            "receipt persistence failure never converts an external side effect into a retryable success",
                            receipt=row.receipt, retry_allowed=retry)

    def _stale_lease(self) -> FailureResult:
        row = self.sim.create("stale-lease")
        self.sim.claim(row, "worker-a")
        self.sim.crash(row, FailurePoint.STALE_LEASE)
        retry = self.sim.blind_retry(row, "worker-b")
        return self._result(FailurePoint.STALE_LEASE, row.uncertain and not retry,
                            "stale execution ownership is reconciled before another worker may execute",
                            state=row.state, retry_allowed=retry)
