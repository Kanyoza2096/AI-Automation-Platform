"""C10 — business vertical certification.

Inspection-only certification for the three primary KDO verticals and their
shared governance/execution substrate. It never grants authority or calls a
provider.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

class C10Status(str, Enum):
    PASS = "pass"
    REVIEW = "review"
    BLOCKED = "blocked"

@dataclass(frozen=True)
class C10Check:
    name: str
    status: C10Status
    evidence: str

@dataclass(frozen=True)
class C10Certification:
    checks: tuple[C10Check, ...]
    @property
    def status(self) -> C10Status:
        if any(c.status == C10Status.BLOCKED for c in self.checks):
            return C10Status.BLOCKED
        if any(c.status == C10Status.REVIEW for c in self.checks):
            return C10Status.REVIEW
        return C10Status.PASS
    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "checks": [c.__dict__ | {"status": c.status.value} for c in self.checks]}

def certify_repository(root: str | Path) -> C10Certification:
    root = Path(root)
    checks: list[C10Check] = []
    def source(rel):
        return (root / rel).read_text(encoding="utf-8")

    content = source("services/v70_6_content_marketing_operating_loop.py")
    sales = source("services/sales_orchestration.py")
    auto = source("core/autonomous_loop.py") + source("services/v71_16_autonomous_loop_workforce_bridge.py")
    outbox = source("core/publish_outbox.py")
    scheduler = source("scheduler/brand_scheduler.py")
    council = source("council/intelligence/decision_router.py") + source("council/orchestration/council_orchestrator.py")
    kernel = source("services/action_kernel.py")

    checks.append(C10Check("marketing_content_to_action_kernel", C10Status.PASS if 'action_type="publish_content"' in content and "requires the governed Action Kernel" in content else C10Status.BLOCKED, "content vertical delegates consequential publishing to Action Kernel"))
    checks.append(C10Check("sales_to_action_kernel", C10Status.PASS if 'EXECUTION_PLANE = "action_kernel_delegate"' in sales and "build_default_action_kernel" in sales else C10Status.BLOCKED, "sales orchestrator delegates execution to Action Kernel"))
    checks.append(C10Check("autonomous_publish_workforce_route", C10Status.PASS if "workforce.proposal" in auto and "execute_run" in auto and "durable workforce run required" in auto else C10Status.BLOCKED, "autonomous publishing is proposal/workforce mediated, not a direct provider path"))
    checks.append(C10Check("scheduled_post_outbox_identity", C10Status.PASS if "scheduled_post_id" in outbox and "_finalize_scheduled_post" in outbox else C10Status.BLOCKED, "scheduled-post identity is carried into outbox reconciliation"))
    checks.append(C10Check("scheduled_claim_recovery", C10Status.PASS if "recover_scheduled_post_claims" in scheduler and 'eq("status", "queued")' in scheduler else C10Status.BLOCKED, "expired claims are recovered without resetting outbox-owned work"))
    checks.append(C10Check("destructive_council_floor", C10Status.PASS if "proposal.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)" in council and "return 3" in council else C10Status.BLOCKED, "high/critical proposals cannot enter auto-approval tier"))
    checks.append(C10Check("publish_contract_is_governed", C10Status.PASS if '"publish_content", risk_level="high"' in kernel and "requires_approval=True" in kernel else C10Status.BLOCKED, "publish_content has an explicit high-risk approval contract"))
    checks.append(C10Check("external_live_evidence", C10Status.REVIEW, "live PostgreSQL/provider execution is an external certification dependency, not inferred from source inspection"))
    return C10Certification(tuple(checks))
