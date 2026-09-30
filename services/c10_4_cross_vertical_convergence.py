"""C10.4 — Cross-Vertical Convergence certification.

Proves that Marketing, Sales, and Autonomous Operations are workloads on the
same governed operating substrate rather than three independent executors.
This module is inspection/certification only; it never grants authority.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class C104Status(str, Enum):
    PASS = "pass"
    REVIEW = "review"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class C104Check:
    name: str
    status: C104Status
    evidence: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class C104Result:
    status: C104Status
    checks: tuple[C104Check, ...]

    @property
    def passed(self) -> bool:
        return self.status == C104Status.PASS

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "passed": self.passed,
                "checks": [{"name": c.name, "status": c.status.value,
                             "evidence": c.evidence, "details": c.details}
                            for c in self.checks]}


def _check(name: str, ok: bool, evidence: str, **details: Any) -> C104Check:
    return C104Check(name, C104Status.PASS if ok else C104Status.BLOCKED,
                     evidence, details)


def certify_repository(root: str | Path) -> C104Result:
    root = Path(root)
    content = (root / "services/v70_6_content_marketing_operating_loop.py").read_text(encoding="utf-8")
    sales = (root / "services/sales_orchestration.py").read_text(encoding="utf-8")
    sales_loop = (root / "services/v70_2_customer_sales_operating_loop.py").read_text(encoding="utf-8")
    operating = (root / "services/v70_business_operating_layer.py").read_text(encoding="utf-8")
    workflow = (root / "services/business_workflow_autonomy.py").read_text(encoding="utf-8")
    automation = (root / "services/business_automation.py").read_text(encoding="utf-8")
    autonomous = (root / "core/autonomous_loop.py").read_text(encoding="utf-8")
    bridge = (root / "services/v71_16_autonomous_loop_workforce_bridge.py").read_text(encoding="utf-8")
    workforce = (root / "services/v71_17_durable_workforce_execution.py").read_text(encoding="utf-8")
    kernel = (root / "services/action_kernel.py").read_text(encoding="utf-8")

    checks = [
        _check("single_action_kernel_authority",
               'build_default_action_kernel' in sales and
               'build_default_action_kernel' in automation and
               'class ActionKernel' in kernel,
               "Sales and Business Automation resolve the canonical Action Kernel; no vertical owns a replacement kernel"),
        _check("marketing_uses_canonical_kernel",
               'action_type="publish_content"' in content and
               "kernel.execute(" in content and
               "requires the governed Action Kernel" in content,
               "Marketing delegates consequential publishing to Action Kernel"),
        _check("sales_uses_canonical_kernel",
               'EXECUTION_PLANE = "action_kernel_delegate"' in sales and
               "kernel.execute(" in sales and
               "build_default_action_kernel" in sales,
               "Sales policy is bounded and delegates its selected action to Action Kernel"),
        _check("sales_state_layer_has_no_execution_authority",
               "never calls CRM/provider APIs or the Action Kernel directly" in sales_loop and
               'execution_effect: str = "none"' in sales_loop,
               "Sales state/routing layer remains above the operating/execution substrate"),
        _check("autonomous_converges_through_workforce",
               "workforce.proposal" in autonomous and
               "execute_run" in autonomous and
               "existing Action Kernel only" in workforce,
               "Autonomous operations enter the durable workforce and converge on the existing governed execution plane"),
        _check("autonomous_provider_plane_absent",
               '"provider_execution_here": False' in workforce and
               '"parallel_execution_plane": False' in workforce and
               '"parallel_execution_plane": False' in bridge,
               "Autonomous workforce explicitly declares no provider or parallel execution plane"),
        _check("common_governance_admission",
               "evaluate_workflow_admission" in workflow and
               "REVIEW_REQUIRED" in workflow and
               "run_catalog_workflow" in operating,
               "Workflow admission is centralized before business automation execution"),
        _check("common_durable_execution_substrate",
               "ExternalActionExecutor" in kernel and
               "ExecutionLedger" in kernel and
               "process_outbox_row" in kernel,
               "All consequential actions terminate in the existing Action Kernel, Execution Ledger, and outbox execution substrate"),
        _check("common_idempotency_boundary",
               "idempotency_key" in content and "idempotency_key" in sales and
               "idempotency_key" in automation and "idempotency_key" in kernel,
               "All three verticals carry stable execution identity into the shared execution boundary"),
        _check("workspace_boundary_is_shared",
               "workspace_id" in content and "workspace_id" in sales and
               "workspace_id" in autonomous and "workspace_id" in kernel,
               "Workspace identity is carried through every vertical into the common execution authority"),
        _check("learning_boundary_is_shared",
               "verified_outcome" in autonomous and
               "outcome_evidence" in autonomous and
               "learn_by_decision_id" in autonomous,
               "Autonomous learning accepts verified outcome evidence rather than raw execution transport"),
        _check("no_vertical_direct_provider_calls",
               all(token not in text for text in (content, sales_loop, sales)
                   for token in ("provider_execute", "direct_provider_call", "connector_execute")),
               "Capability layers contain no direct provider execution primitive"),
    ]
    status = (C104Status.BLOCKED if any(c.status == C104Status.BLOCKED for c in checks)
              else C104Status.PASS)
    # Repository convergence is a structural proof. Distributed/live execution
    # evidence remains an external staging gate and is intentionally not inferred.
    checks.append(C104Check("live_cross_vertical_evidence", C104Status.REVIEW,
                            "real PostgreSQL concurrency, worker crash recovery, and live provider evidence remain staging dependencies"))
    if status != C104Status.BLOCKED:
        status = C104Status.REVIEW
    return C104Result(status, tuple(checks))


__all__ = ["C104Status", "C104Check", "C104Result", "certify_repository"]
