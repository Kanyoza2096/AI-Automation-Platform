"""C10.3 — Autonomous Business Operations vertical certification."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from services.v71_20_autonomous_execution_certification import AutonomousExecutionCertification, CertificationCheck, CertificationStatus

class C103Status(str, Enum):
    PASS = "pass"
    REVIEW = "review"
    BLOCKED = "blocked"

@dataclass(frozen=True)
class C103Result:
    status: C103Status
    checks: tuple[CertificationCheck, ...] = field(default_factory=tuple)
    @property
    def passed(self) -> bool:
        return self.status == C103Status.PASS
    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "passed": self.passed, "checks": [{"name": c.name, "status": c.status.value, "evidence": c.evidence, "details": dict(c.details)} for c in self.checks]}

def _check(name: str, ok: bool, evidence: str, details: Mapping[str, Any] | None = None) -> CertificationCheck:
    return CertificationCheck(name, CertificationStatus.PASS if ok else CertificationStatus.FAIL, evidence, details or {})

def certify_repository_convergence(root: str | Path) -> tuple[CertificationCheck, ...]:
    root = Path(root)
    loop = (root / "core/autonomous_loop.py").read_text(encoding="utf-8")
    content = (root / "core/autonomous_content.py").read_text(encoding="utf-8")
    bridge = (root / "services/v71_16_autonomous_loop_workforce_bridge.py").read_text(encoding="utf-8")
    execution = (root / "services/v71_17_durable_workforce_execution.py").read_text(encoding="utf-8")
    convergence = (root / "services/v71_17_durable_workforce_execution_convergence.py").read_text(encoding="utf-8")
    envelope = (root / "services/business_autonomy_envelope.py").read_text(encoding="utf-8")
    evaluation = (root / "services/v71_5_agent_evaluation.py").read_text(encoding="utf-8")
    return (
        _check("observe_to_workforce_proposal", "publish_request" in content and "_handle_publish_request" in loop and "workforce.proposal" in loop, "autonomous content emits publish_request and AutonomousLoop routes it to the workforce bridge"),
        _check("legacy_publish_command_is_fenced", "durable_workforce_run_required" in loop and "Legacy publish_command fenced" in loop, "bare legacy publish_command cannot fall back to direct publishing"),
        _check("workforce_is_not_an_execution_authority", '"authority_granted_by_bridge": False' in bridge, "V71.16 bridge explicitly reports authority_granted_by_bridge=False"),
        _check("execution_converges_on_action_kernel", "existing Action Kernel only" in execution and "V71.9" in convergence, "V71.17 delegates into the existing governed workflow path; provider execution is downstream"),
        _check("envelope_cannot_grant_authority", 'authority: str = field(default="none", init=False)' in envelope and 'execution_effect: str = field(default="none", init=False)' in envelope, "autonomy envelope is declarative and has no authority/execution effect"),
        _check("revocation_is_rechecked_at_execution", "envelope.is_active" in envelope and "reconciliation_required" in execution, "envelope validity is evaluated and stale worker state is fenced for reconciliation"),
        _check("failure_recovery_is_explicit", "mark_stale_for_reconciliation" in execution and "reconcile_stale" in execution, "expired autonomous work is reconciled rather than blindly retried"),
        _check("outcome_evaluation_exists", "outcome_evidence" in evaluation and "outcome_alignment" in evaluation, "agent evaluation consumes outcome evidence"),
        _check("verified_workforce_outcomes_feed_learning", "workforce.execution.result" in loop and "verified_outcome" in loop and "outcome_evidence" in loop and "_handle_outcome_recorded" in loop, "workforce execution results can enter learning only through an explicit verified-outcome evidence boundary"),
        _check("no_parallel_provider_plane", '"provider_execution_here": False' in execution, "V71.17 declares provider_execution_here=False and Action Kernel as execution authority"),
    )

def certify_c10_3(*, root: str | Path, admission_certification: AutonomousExecutionCertification | None = None) -> C103Result:
    checks = list(certify_repository_convergence(root))
    if admission_certification is not None:
        checks.extend(admission_certification.checks)
    checks.append(CertificationCheck("distributed_autonomy_evidence", CertificationStatus.NOT_PROVEN, "real PostgreSQL worker concurrency and crash injection require staging evidence; atomic budget reservation is now implemented by migration 111 but still needs live concurrency proof"))
    status = C103Status.BLOCKED if any(c.status == CertificationStatus.FAIL for c in checks) else C103Status.REVIEW if any(c.status == CertificationStatus.NOT_PROVEN for c in checks) else C103Status.PASS
    return C103Result(status, tuple(checks))

__all__ = ["C103Status", "C103Result", "certify_repository_convergence", "certify_c10_3"]
