"""KDO Phase 9.7 — Resilience Test Contracts.

Provides deterministic failure-injection scenarios and assertions around
Phase 9 reliability/reconciliation behavior. This harness is a test model:
it does not execute providers or grant authority.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple
from uuid import uuid4

from .phase9_reliability import DependencyState
from .phase9_failure_policy import OperationRisk, PolicyDecision, RecoveryPolicy, evaluate
from .phase9_reconciliation import (
    AuthoritativeObservation,
    ExpectedState,
    ReconciliationStatus,
    reconcile,
)


SCHEMA_VERSION = "phase9.resilience.v1"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class FailureMode(str, Enum):
    TIMEOUT = "TIMEOUT"
    CONNECTION_FAILURE = "CONNECTION_FAILURE"
    ERROR_RESPONSE = "ERROR_RESPONSE"
    STALE_DATA = "STALE_DATA"
    INCONSISTENT_DATA = "INCONSISTENT_DATA"
    PARTIAL_OUTAGE = "PARTIAL_OUTAGE"
    RECOVERY = "RECOVERY"


class ResilienceOutcome(str, Enum):
    SAFE = "SAFE"
    BLOCKED = "BLOCKED"
    HOLD = "HOLD"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class FailureInjection:
    workspace_id: str
    dependency_id: str
    mode: FailureMode
    injected_at: str
    injection_id: str = field(default_factory=lambda: _id("inject"))
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not self.workspace_id.strip() or not self.dependency_id.strip():
            raise ValueError("workspace_id and dependency_id are required")
        if not self.injected_at.strip():
            raise ValueError("injected_at is required")
        if self.authority != "none":
            raise ValueError("failure injection cannot carry authority")


@dataclass(frozen=True)
class ResilienceScenario:
    name: str
    dependency_id: str
    initial_state: DependencyState
    failure_mode: FailureMode
    risk: OperationRisk
    read_only: bool = False
    expected_policy_decision: PolicyDecision = PolicyDecision.HOLD
    expected_outcome: ResilienceOutcome = ResilienceOutcome.BLOCKED
    scenario_id: str = field(default_factory=lambda: _id("scenario"))
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not self.name.strip() or not self.dependency_id.strip():
            raise ValueError("name and dependency_id are required")
        if self.authority != "none":
            raise ValueError("scenario cannot carry authority")


@dataclass(frozen=True)
class ResilienceResult:
    scenario_id: str
    outcome: ResilienceOutcome
    policy_decision: PolicyDecision
    reconciliation_status: Optional[ReconciliationStatus]
    assertions: Tuple[str, ...]
    passed: bool
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if self.authority != "none":
            raise ValueError("resilience result cannot carry authority")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "scenario_id": self.scenario_id,
            "outcome": self.outcome.value,
            "policy_decision": self.policy_decision.value,
            "reconciliation_status": (
                self.reconciliation_status.value
                if self.reconciliation_status else None
            ),
            "assertions": list(self.assertions),
            "passed": self.passed,
            "authority": self.authority,
        }


def run_scenario(
    scenario: ResilienceScenario,
    *,
    workspace_id: str = "ws_1",
    observed_state: Optional[dict] = None,
) -> ResilienceResult:
    """Evaluate a deterministic resilience scenario.

    No external side effects occur.
    """
    policy = RecoveryPolicy(
        workspace_id=workspace_id,
        dependency_id=scenario.dependency_id,
    )

    decision = evaluate(
        policy,
        dependency_state=scenario.initial_state,
        risk=scenario.risk,
        read_only=scenario.read_only,
    )

    reconciliation_status = None
    assertions = []

    if scenario.failure_mode in {
        FailureMode.STALE_DATA,
        FailureMode.INCONSISTENT_DATA,
    }:
        expected = ExpectedState(
            workspace_id=workspace_id,
            resource_ref=f"dependency:{scenario.dependency_id}",
            state={"status": "healthy", "version": 1},
        )
        observed = AuthoritativeObservation(
            workspace_id=workspace_id,
            resource_ref=f"dependency:{scenario.dependency_id}",
            state=observed_state or {"status": "healthy", "version": 0},
            observed_at="2026-10-05T08:00:00Z",
            evidence_ref="resilience-observation",
        )
        finding = reconcile(expected, observed)
        reconciliation_status = finding.status
        assertions.append(f"reconciliation={finding.status.value}")

    if decision.decision in {
        PolicyDecision.BLOCK,
        PolicyDecision.SECURITY_HOLD,
        PolicyDecision.QUARANTINE,
    }:
        outcome = ResilienceOutcome.BLOCKED
    elif decision.decision == PolicyDecision.RECONCILIATION_REQUIRED:
        outcome = ResilienceOutcome.RECONCILIATION_REQUIRED
    elif decision.decision in {
        PolicyDecision.HOLD,
        PolicyDecision.RECOVERY_REQUIRED,
    }:
        outcome = ResilienceOutcome.HOLD
    else:
        outcome = ResilienceOutcome.SAFE

    if reconciliation_status == ReconciliationStatus.DRIFT_DETECTED:
        outcome = ResilienceOutcome.RECONCILIATION_REQUIRED

    passed = (
        outcome == scenario.expected_outcome
        and decision.decision == scenario.expected_policy_decision
    )
    assertions.append(f"policy={decision.decision.value}")
    assertions.append(f"outcome={outcome.value}")

    return ResilienceResult(
        scenario_id=scenario.scenario_id,
        outcome=outcome,
        policy_decision=decision.decision,
        reconciliation_status=reconciliation_status,
        assertions=tuple(assertions),
        passed=passed,
    )
