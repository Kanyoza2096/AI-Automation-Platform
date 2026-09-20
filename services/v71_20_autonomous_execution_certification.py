"""V71.20 — C8.4 autonomous execution certification.

Certification-only harness for bounded autonomy.  It does not grant authority,
execute providers, or replace the Action Kernel.  It checks that autonomous
execution remains fail-closed at the execution boundary and explicitly reports
which distributed guarantees still require a real durable runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional

from services.business_autonomy_envelope import (
    AutonomyMode, BusinessAutonomyEnvelope, EnvelopeState, RiskBoundary,
    SuccessMetric,
)
from services.v71_9_bounded_autonomous_operations import (
    AutonomousRunBlock, AutonomousRunDisposition, AutonomousRunStatus,
    BoundedAutonomousOperationsRegistry,
)


class CertificationStatus(str, Enum):
    PASS = "pass"
    NOT_PROVEN = "not_proven"
    FAIL = "fail"


@dataclass(frozen=True)
class CertificationCheck:
    name: str
    status: CertificationStatus
    evidence: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AutonomousExecutionCertification:
    checks: tuple[CertificationCheck, ...]

    @property
    def passed(self) -> bool:
        return all(c.status == CertificationStatus.PASS for c in self.checks)

    @property
    def status(self) -> CertificationStatus:
        if any(c.status == CertificationStatus.FAIL for c in self.checks):
            return CertificationStatus.FAIL
        if any(c.status == CertificationStatus.NOT_PROVEN for c in self.checks):
            return CertificationStatus.NOT_PROVEN
        return CertificationStatus.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "passed": self.passed,
            "checks": [
                {"name": c.name, "status": c.status.value,
                 "evidence": c.evidence, "details": dict(c.details)}
                for c in self.checks
            ],
        }


def certify_admission_boundaries(
    operations: BoundedAutonomousOperationsRegistry,
    *,
    run_id: str,
    envelope: BusinessAutonomyEnvelope,
    workflow: Mapping[str, Any],
) -> AutonomousExecutionCertification:
    """Exercise fail-closed admission invariants without invoking a provider."""
    checks: list[CertificationCheck] = []
    admission = operations.admit(run_id, envelope=envelope, workflow=workflow,
                                 risk=str(workflow.get("risk") or "low"))
    checks.append(CertificationCheck(
        "authority_is_not_granted_by_admission",
        CertificationStatus.PASS if not admission.authority_granted and not admission.execution_requested else CertificationStatus.FAIL,
        "admission returned no authority or execution effect",
    ))

    high = dict(workflow, risk="high")
    high_admission = operations.admit(run_id, envelope=envelope, workflow=high, risk="high")
    checks.append(CertificationCheck(
        "high_risk_never_becomes_autonomous_execution",
        CertificationStatus.PASS if high_admission.status == AutonomousRunStatus.BLOCKED and
        high_admission.disposition == AutonomousRunDisposition.GOVERNANCE_REVIEW_REQUIRED else CertificationStatus.FAIL,
        "high-risk workflow remains behind existing governance",
        {"status": high_admission.status.value, "disposition": high_admission.disposition.value},
    ))

    revoked = BusinessAutonomyEnvelope(
        workspace_id=envelope.workspace_id,
        objective=envelope.objective,
        success_metrics=envelope.success_metrics,
        allowed_actions=envelope.allowed_actions,
        scope=envelope.scope,
        risk_boundary=envelope.risk_boundary,
        resource_limits=envelope.resource_limits,
        escalation_triggers=envelope.escalation_triggers,
        mode=envelope.mode,
        valid_from=envelope.valid_from,
        valid_until=envelope.valid_until,
        state=EnvelopeState.REVOKED,
        goal_id=envelope.goal_id,
        envelope_id=envelope.envelope_id,
        version=envelope.version + 1,
    )
    revoked_admission = operations.admit(run_id, envelope=revoked, workflow=workflow,
                                         risk=str(workflow.get("risk") or "low"))
    checks.append(CertificationCheck(
        "revoked_envelope_blocks_execution",
        CertificationStatus.PASS if revoked_admission.status == AutonomousRunStatus.BLOCKED and
        "envelope_not_active" in revoked_admission.reasons else CertificationStatus.FAIL,
        "authoritative envelope state is rechecked before engine admission",
        {"reasons": list(revoked_admission.reasons)},
    ))

    checks.append(CertificationCheck(
        "budget_concurrency_reservation",
        CertificationStatus.NOT_PROVEN,
        "current budget registry performs check then post-execution usage accounting; atomic reservation requires a durable shared store",
    ))

    checks.append(CertificationCheck(
        "real_worker_authority_under_concurrency",
        CertificationStatus.NOT_PROVEN,
        "requires real PostgreSQL/workforce lease concurrency and crash injection",
    ))
    return AutonomousExecutionCertification(tuple(checks))


__all__ = ["CertificationStatus", "CertificationCheck", "AutonomousExecutionCertification", "certify_admission_boundaries"]
