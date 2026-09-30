"""C11 — Final Architecture Certification.

This gate certifies repository-level architectural invariants and explicitly
separates them from evidence that requires real infrastructure/providers.
It never grants execution authority and never upgrades missing live evidence.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import re
from typing import Any

@dataclass(frozen=True)
class C11Check:
    name: str
    status: str
    evidence: str
    details: dict[str, Any] | None = None

@dataclass(frozen=True)
class C11Result:
    status: str
    checks: tuple[C11Check, ...]
    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "checks": [asdict(c) for c in self.checks]}

def _check(name: str, ok: bool, evidence: str, **details: Any) -> C11Check:
    return C11Check(name, "PASS" if ok else "BLOCKED", evidence, details or None)

def certify_repository(root: str | Path) -> C11Result:
    root = Path(root)
    kernel = (root / "services/action_kernel.py").read_text(encoding="utf-8")
    ledger = (root / "services/execution_ledger.py").read_text(encoding="utf-8")
    executor = (root / "services/external_action_executor.py").read_text(encoding="utf-8")
    autonomous = (root / "core/autonomous_loop.py").read_text(encoding="utf-8")
    workforce = (root / "services/v71_17_durable_workforce_execution.py").read_text(encoding="utf-8")
    runtime = (root / "services/v71_10_durable_workforce_runtime.py").read_text(encoding="utf-8")
    bounded = (root / "services/v71_9_bounded_autonomous_operations.py").read_text(encoding="utf-8")
    envelope = (root / "services/business_autonomy_envelope.py").read_text(encoding="utf-8")
    budget = (root / "services/v71_6_agent_budgets.py").read_text(encoding="utf-8")
    migration = (root / "migrations/111_v71_21_atomic_agent_budget_reservations.sql").read_text(encoding="utf-8")
    recovery = (root / "migrations/112_v71_21_budget_uncertain_recovery.sql").read_text(encoding="utf-8")
    version = (root / "platform_version.py").read_text(encoding="utf-8")
    checks = [
        _check("canonical_execution_authority", "class ActionKernel" in kernel and "ExternalActionExecutor" in kernel and "ExecutionLedger" in kernel, "Action Kernel is the common consequential-action boundary"),
        _check("durable_side_effect_boundary", "ExecutionLedger" in executor and "uncertain" in executor.lower() and "process_outbox_row" in kernel, "External effects have durable identity/uncertainty handling and the kernel owns outbox processing"),
        _check("autonomy_is_not_authority", 'authority: str = field(default="none", init=False)' in envelope and 'execution_effect: str = field(default="none", init=False)' in envelope, "Autonomy envelopes cannot grant authority or execution effects"),
        _check("execution_boundary_rechecks_governance", "evaluate_workflow_admission" in bounded and "self.admit(" in bounded and "run_catalog_workflow" in bounded, "Durable autonomous execution re-enters bounded governance admission before the governed workflow execution call"),
        _check("autonomous_no_provider_plane", '"provider_execution_here": False' in workforce and '"parallel_execution_plane": False' in workforce, "Autonomous workforce contains no provider execution plane"),
        _check("workspace_identity_is_explicit", "workspace_id" in kernel and "workspace_id" in autonomous and "workspace_id" in workforce, "Workspace identity is explicit at execution boundaries"),
        _check("atomic_budget_reservation_exists", "reserve_agent_budget" in migration and "used_actions + reserved_actions + p_actions" in migration and "DurableAgentBudgetReservations" in budget, "Budget admission uses database-side atomic reservation when durable infrastructure is present"),
        _check("uncertain_budget_recovery_is_conservative", "status='uncertain'" in recovery and "used_actions=b.used_actions+m.actions" in recovery and "reserved_actions=b.reserved_actions-m.actions" in recovery, "Expired reservations transfer capacity from reserved to consumed/uncertain instead of stranding it"),
        _check("legacy_bypass_is_fenced", "durable_workforce_run_required" in autonomous and "Legacy publish_command fenced" in autonomous, "Legacy autonomous publish commands cannot bypass durable governed execution"),
        _check("release_identity_is_canonical", bool(re.search(r'^PLATFORM_VERSION\s*=\s*[\"\'][^\"\']+[\"\']$', version, re.M)) and "from platform_version import PLATFORM_VERSION" in (root / "main.py").read_text(encoding="utf-8"), "Deployable identity is sourced from one canonical version module"),
    ]
    live = C11Check("live_system_evidence", "NOT_PROVEN", "Real PostgreSQL concurrency, crash recovery, Redis recovery, and live provider execution require staging evidence")
    checks.append(live)
    status = "BLOCKED" if any(c.status == "BLOCKED" for c in checks) else "REVIEW"
    return C11Result(status, tuple(checks))

__all__ = ["C11Check", "C11Result", "certify_repository"]
