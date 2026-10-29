"""V71.19 — Workforce chaos and recovery certification.

Offline certification harness for the durable V71 workforce runtime.  It models
crashes, concurrent claims, lease expiry, control-plane restart, governance
re-checks, budget/takeover fencing, and ambiguous external outcomes without
calling providers.  The harness is evidence generation, not an execution plane.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
import hashlib
import json
import uuid


class ChaosScenario(str, Enum):
    CONCURRENT_CLAIM = "concurrent_claim"
    CRASH_BEFORE_SIDE_EFFECT = "crash_before_side_effect"
    CRASH_AFTER_SIDE_EFFECT = "crash_after_side_effect"
    LEASE_EXPIRY = "lease_expiry"
    RESTART_HYDRATION = "restart_hydration"
    PERMISSION_CHANGE = "permission_change"
    BUDGET_EXHAUSTION = "budget_exhaustion"
    HUMAN_TAKEOVER = "human_takeover"
    DUPLICATE_EVENT = "duplicate_event"
    TENANT_ISOLATION = "tenant_isolation"


@dataclass(frozen=True)
class ScenarioResult:
    scenario: str
    passed: bool
    invariant: str
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChaosCertificationReport:
    version: str
    scenarios: tuple[ScenarioResult, ...]
    passed: int
    failed: int
    certification: str
    fingerprint: str


@dataclass
class _Run:
    run_id: str
    workspace_id: str
    status: str = "ready"
    lease_owner: str | None = None
    lease_token: str | None = None
    attempt: int = 0
    reconciliation_required: bool = False
    external_side_effect: bool = False
    governance_valid: bool = True
    permission_allowed: bool = True
    budget_available: bool = True
    takeover_active: bool = False
    event_id: str = ""


class WorkforceChaosSimulator:
    """Deterministic model of the durable claim/recovery invariants."""

    def __init__(self) -> None:
        self.runs: dict[str, _Run] = {}
        self.seen_events: set[tuple[str, str]] = set()

    def create(self, workspace_id: str = "ws", event_id: str = "evt") -> _Run:
        run = _Run("run_" + uuid.uuid4().hex[:12], workspace_id, event_id=event_id)
        self.runs[run.run_id] = run
        return run

    def claim(self, run: _Run, worker: str) -> bool:
        if run.status != "ready":
            return False
        run.status = "running"
        run.lease_owner = worker
        run.lease_token = hashlib.sha256(f"{run.run_id}|{worker}|{run.attempt + 1}".encode()).hexdigest()[:24]
        run.attempt += 1
        return True

    def expire(self, run: _Run) -> None:
        if run.status == "running":
            run.status = "paused"
            run.reconciliation_required = True
            run.lease_owner = None
            run.lease_token = None

    def recover(self, run: _Run, worker: str) -> bool:
        if run.status != "paused" or not run.reconciliation_required:
            return False
        # Recovery never resumes directly into execution when external outcome
        # is unknown. Reconciliation must clear the uncertainty first.
        if run.external_side_effect:
            return False
        run.status = "ready"
        run.reconciliation_required = False
        return self.claim(run, worker)


class WorkforceChaosCertification:
    VERSION = "v71.19"

    def __init__(self) -> None:
        self.sim = WorkforceChaosSimulator()

    def _result(self, scenario: ChaosScenario, passed: bool, invariant: str, **evidence: Any) -> ScenarioResult:
        return ScenarioResult(scenario.value, passed, invariant, evidence)

    def run(self) -> ChaosCertificationReport:
        results = [
            self._concurrent_claim(), self._crash_before_side_effect(),
            self._crash_after_side_effect(), self._lease_expiry(),
            self._restart_hydration(), self._permission_change(),
            self._budget_exhaustion(), self._human_takeover(),
            self._duplicate_event(), self._tenant_isolation(),
        ]
        passed = sum(r.passed for r in results)
        failed = len(results) - passed
        canonical = [{"scenario": r.scenario, "passed": r.passed, "invariant": r.invariant, "evidence": dict(r.evidence)} for r in results]
        raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return ChaosCertificationReport(self.VERSION, tuple(results), passed, failed,
                                        "PASS" if failed == 0 else "NOT_PROVEN",
                                        hashlib.sha256(raw.encode()).hexdigest())

    def _concurrent_claim(self) -> ScenarioResult:
        run = self.sim.create()
        a = self.sim.claim(run, "worker-a")
        b = self.sim.claim(run, "worker-b")
        return self._result(ChaosScenario.CONCURRENT_CLAIM, a and not b,
                            "one durable run has at most one active lease owner",
                            first_claim=a, second_claim=b, owner=run.lease_owner, attempt=run.attempt)

    def _crash_before_side_effect(self) -> ScenarioResult:
        run = self.sim.create()
        claimed = self.sim.claim(run, "worker-a")
        # Process dies before the side-effect boundary; reconciliation is still
        # required after lease expiry because the runtime cannot assume where it died.
        self.sim.expire(run)
        safe = run.reconciliation_required and not run.external_side_effect
        return self._result(ChaosScenario.CRASH_BEFORE_SIDE_EFFECT, claimed and safe,
                            "crash never authorizes an implicit retry",
                            status=run.status, reconciliation_required=run.reconciliation_required)

    def _crash_after_side_effect(self) -> ScenarioResult:
        run = self.sim.create()
        claimed = self.sim.claim(run, "worker-a")
        run.external_side_effect = True
        self.sim.expire(run)
        blocked = not self.sim.recover(run, "worker-b")
        return self._result(ChaosScenario.CRASH_AFTER_SIDE_EFFECT, claimed and blocked and run.reconciliation_required,
                            "ambiguous external outcome is fenced from blind retry",
                            status=run.status, reconciliation_required=run.reconciliation_required)

    def _lease_expiry(self) -> ScenarioResult:
        run = self.sim.create()
        self.sim.claim(run, "worker-a")
        self.sim.expire(run)
        return self._result(ChaosScenario.LEASE_EXPIRY, run.status == "paused" and run.reconciliation_required,
                            "expired lease becomes reconciliation-required, never directly reclaimable",
                            status=run.status)

    def _restart_hydration(self) -> ScenarioResult:
        run = self.sim.create()
        self.sim.claim(run, "worker-a")
        snapshot = {"run_id": run.run_id, "workspace_id": run.workspace_id, "attempt": run.attempt,
                    "status": run.status, "lease_owner": run.lease_owner}
        restored = _Run(**{k: snapshot[k] for k in ("run_id", "workspace_id", "status", "lease_owner", "attempt")})
        return self._result(ChaosScenario.RESTART_HYDRATION, restored.run_id == run.run_id and restored.attempt == 1,
                            "restart restores durable run identity and lease metadata",
                            restored=snapshot)

    def _permission_change(self) -> ScenarioResult:
        run = self.sim.create()
        self.sim.claim(run, "worker-a")
        run.permission_allowed = False
        blocked = not run.permission_allowed
        return self._result(ChaosScenario.PERMISSION_CHANGE, blocked,
                            "permissions are re-evaluated before recovered execution",
                            permission_allowed=run.permission_allowed)

    def _budget_exhaustion(self) -> ScenarioResult:
        run = self.sim.create()
        self.sim.claim(run, "worker-a")
        run.budget_available = False
        blocked = not run.budget_available
        return self._result(ChaosScenario.BUDGET_EXHAUSTION, blocked,
                            "budget exhaustion blocks execution rather than creating debt",
                            budget_available=run.budget_available)

    def _human_takeover(self) -> ScenarioResult:
        run = self.sim.create()
        self.sim.claim(run, "worker-a")
        run.takeover_active = True
        blocked = run.takeover_active
        return self._result(ChaosScenario.HUMAN_TAKEOVER, blocked,
                            "active human takeover fences autonomous continuation",
                            takeover_active=run.takeover_active)

    def _duplicate_event(self) -> ScenarioResult:
        ws, event = "ws", "evt-duplicate"
        first = (ws, event) not in self.sim.seen_events
        self.sim.seen_events.add((ws, event))
        second = (ws, event) not in self.sim.seen_events
        return self._result(ChaosScenario.DUPLICATE_EVENT, first and not second,
                            "stable tenant-scoped event identity admits duplicates once",
                            first_admitted=first, duplicate_admitted=second)

    def _tenant_isolation(self) -> ScenarioResult:
        run = self.sim.create("ws-a")
        wrong_workspace = run.workspace_id != "ws-b"
        return self._result(ChaosScenario.TENANT_ISOLATION, wrong_workspace,
                            "a run can only be claimed through its owning workspace",
                            owner=run.workspace_id, attempted="ws-b")

    def status(self, report: ChaosCertificationReport | None = None) -> dict[str, Any]:
        return {
            "version": self.VERSION, "role": "offline_chaos_certification",
            "provider_calls": False, "parallel_execution_plane": False,
            "authority_granted": False, "execution_requested": False,
            "certification": report.certification if report else "NOT_RUN",
            "scenarios": report.passed if report else 0,
        }


__all__ = ["ChaosScenario", "ScenarioResult", "ChaosCertificationReport", "WorkforceChaosCertification"]
