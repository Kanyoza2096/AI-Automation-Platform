"""C10.2 — Sales / Customer Operations certification.

This gate certifies the Sales vertical's architecture and deterministic behavior.
It distinguishes repository evidence from live PostgreSQL/provider evidence and
never grants authority or executes external side effects itself.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

class SalesC102Status(str, Enum):
    PASS = "pass"
    REVIEW = "review"
    BLOCKED = "blocked"

@dataclass(frozen=True)
class SalesC102Check:
    name: str
    status: SalesC102Status
    evidence: str

@dataclass(frozen=True)
class SalesC102Certification:
    checks: tuple[SalesC102Check, ...]
    @property
    def status(self) -> SalesC102Status:
        if any(c.status == SalesC102Status.BLOCKED for c in self.checks):
            return SalesC102Status.BLOCKED
        if any(c.status == SalesC102Status.REVIEW for c in self.checks):
            return SalesC102Status.REVIEW
        return SalesC102Status.PASS
    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "checks": [c.__dict__ | {"status": c.status.value} for c in self.checks]}

def certify_sales_repository(root: str | Path) -> SalesC102Certification:
    root = Path(root)
    sales = (root / "services/v70_2_customer_sales_operating_loop.py").read_text(encoding="utf-8")
    orch = (root / "services/sales_orchestration.py").read_text(encoding="utf-8")
    kernel = (root / "services/action_kernel.py").read_text(encoding="utf-8")
    m107 = (root / "migrations/107_v70_2_sales_event_durable_processing.sql").read_text(encoding="utf-8")
    m108 = (root / "migrations/108_v70_2_sales_atomic_state_transition.sql").read_text(encoding="utf-8")
    m110 = (root / "migrations/110_v70_2_sales_lead_execution_fence.sql").read_text(encoding="utf-8")
    checks = [
        SalesC102Check("sales_loop_operating_layer_boundary", SalesC102Status.PASS if "BusinessOperatingLayer" in sales and "self.operating_layer.handle_event" in sales and "action_kernel" not in sales.lower() else SalesC102Status.BLOCKED, "Sales loop delegates consequential work to the business operating layer."),
        SalesC102Check("event_identity_claim_lease", SalesC102Status.PASS if "claim_v70_sales_event" in sales and "_finalize_event" in sales and "recover_expired_events" in sales else SalesC102Status.BLOCKED, "Sales events use durable identity/lease/finalization primitives."),
        SalesC102Check("lead_execution_fence", SalesC102Status.PASS if "claim_v70_sales_lead_execution" in sales and "release_v70_sales_lead_execution" in sales and "110_v70_2_sales_lead_execution_fence.sql" in m110 or ("claim_v70_sales_lead_execution" in sales and "release_v70_sales_lead_execution" in sales) else SalesC102Status.BLOCKED, "Same-lead events are fenced before the governed business boundary."),
        SalesC102Check("atomic_state_transition", SalesC102Status.PASS if "transition_v70_sales_lead" in sales and "p_expected_version" in m108 else SalesC102Status.BLOCKED, "Durable lead state uses optimistic versioned transition."),
        SalesC102Check("uncertain_failure_convergence", SalesC102Status.PASS if '"uncertain"' in sales and "finalize_v70_sales_event" in m107 else SalesC102Status.BLOCKED, "Ambiguous processing is fenced as uncertain rather than blindly retried."),
        SalesC102Check("bounded_sales_policy", SalesC102Status.PASS if "POLICY = {" in orch and "No bounded sales policy matched" in orch else SalesC102Status.BLOCKED, "Customer intent maps only to allow-listed sales actions."),
        SalesC102Check("outbound_communication_governed", SalesC102Status.PASS if '"customer_communication", risk_level="high"' in kernel and "requires_approval=True" in kernel else SalesC102Status.BLOCKED, "Customer communication remains an explicit high-risk governed action."),
        SalesC102Check("live_infrastructure_evidence", SalesC102Status.REVIEW, "Real PostgreSQL concurrency, RLS and provider execution require staging evidence."),
    ]
    return SalesC102Certification(tuple(checks))
