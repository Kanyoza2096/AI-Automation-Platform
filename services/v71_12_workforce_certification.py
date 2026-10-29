"""V71.12 — Governed AI Workforce certification gate.

Offline certification proves structural/safety invariants only. Live provider,
production persistence, concurrency, credentials, and external side effects
remain NOT_PROVEN unless supplied with independent evidence.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import ast


class CertificationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_PROVEN = "NOT_PROVEN"


@dataclass(frozen=True)
class CertificationFinding:
    control: str
    status: CertificationStatus
    detail: str


@dataclass(frozen=True)
class WorkforceCertificationReport:
    status: CertificationStatus
    findings: tuple[CertificationFinding, ...]


def _finding(control, status, detail):
    return CertificationFinding(control, status, detail)


def _contains_forbidden_execution_surface(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden_modules = {
        "requests", "httpx", "boto3", "openai", "google.generativeai",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name in forbidden_modules for a in node.names):
                return True
        if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
            return True
    return False


def run_offline_certification(root: str | Path | None = None) -> WorkforceCertificationReport:
    base = Path(root) if root else Path(__file__).resolve().parents[1]
    findings: list[CertificationFinding] = []

    modules = {
        "identity": base / "services/v71_1_agent_identity.py",
        "permissions": base / "services/v71_2_agent_capability_permissions.py",
        "memory": base / "services/v71_3_agent_memory.py",
        "planning": base / "services/v71_4_agent_planning.py",
        "evaluation": base / "services/v71_5_agent_evaluation.py",
        "budgets": base / "services/v71_6_agent_budgets.py",
        "takeover": base / "services/v71_7_human_takeover.py",
        "coordination": base / "services/v71_8_multi_agent_coordination.py",
        "autonomy": base / "services/v71_9_bounded_autonomous_operations.py",
        "runtime": base / "services/v71_10_durable_workforce_runtime.py",
        "control": base / "services/v71_11_workforce_observability_control.py",
    }
    missing = [k for k, p in modules.items() if not p.exists()]
    if missing:
        findings.append(_finding("module_integrity", CertificationStatus.FAIL, f"missing: {missing}"))
    else:
        findings.append(_finding("module_integrity", CertificationStatus.PASS, "V71.1–V71.11 modules present"))

    # Workforce layers must not directly own external side effects.
    forbidden = [k for k, p in modules.items() if p.exists() and _contains_forbidden_execution_surface(p)]
    if forbidden:
        findings.append(_finding("execution_boundary", CertificationStatus.FAIL, f"direct external client imports: {forbidden}"))
    else:
        findings.append(_finding("execution_boundary", CertificationStatus.PASS, "workforce modules contain no direct provider client imports"))

    # Explicit source-level authority markers are a useful offline invariant.
    autonomy = modules["autonomy"].read_text(encoding="utf-8") if modules["autonomy"].exists() else ""
    runtime = modules["runtime"].read_text(encoding="utf-8") if modules["runtime"].exists() else ""
    control = modules["control"].read_text(encoding="utf-8") if modules["control"].exists() else ""
    if "run_catalog_workflow" in autonomy and "Action Kernel" in autonomy:
        findings.append(_finding("single_governed_execution_path", CertificationStatus.PASS, "autonomy delegates to the existing governed workflow path"))
    else:
        findings.append(_finding("single_governed_execution_path", CertificationStatus.FAIL, "governed execution delegation markers missing"))

    if "lease" in runtime.lower() and "reconciliation" in runtime.lower():
        findings.append(_finding("durability_and_reconciliation", CertificationStatus.PASS, "durable runtime includes lease and reconciliation controls"))
    else:
        findings.append(_finding("durability_and_reconciliation", CertificationStatus.FAIL, "durable lease/reconciliation controls missing"))

    if "RESUME" in control and "reconciliation" in control.lower() and "authoritative" in control:
        findings.append(_finding("operator_control_boundary", CertificationStatus.PASS, "control plane separates pause/resume from authoritative execution"))
    else:
        findings.append(_finding("operator_control_boundary", CertificationStatus.FAIL, "control boundary markers missing"))

    # These cannot honestly be proven offline.
    for control_name, detail in (
        ("live_provider_execution", "real external provider side effects require live staging evidence"),
        ("production_restart_persistence", "restart/crash recovery requires a real durable deployment"),
        ("multi_worker_concurrency", "atomic race behavior requires concurrent live database execution"),
        ("credential_revocation", "revocation behavior requires live connector credentials"),
        ("provider_rate_limits", "provider throttling behavior requires live provider tests"),
    ):
        findings.append(_finding(control_name, CertificationStatus.NOT_PROVEN, detail))

    if any(f.status == CertificationStatus.FAIL for f in findings):
        overall = CertificationStatus.FAIL
    elif any(f.status == CertificationStatus.NOT_PROVEN for f in findings):
        overall = CertificationStatus.NOT_PROVEN
    else:
        overall = CertificationStatus.PASS
    return WorkforceCertificationReport(overall, tuple(findings))
