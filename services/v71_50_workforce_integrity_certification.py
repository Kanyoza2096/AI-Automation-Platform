"""Phase 7.41 — End-to-end workforce integrity certification.

Offline/architecture certification over the already-canonical workforce chain.
This module is a read-only certification gate: it does not authorize, execute,
repair, provision, call providers, or rewrite canonical history.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import ast
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class IntegrityFinding:
    control: str
    status: str
    detail: str


@dataclass(frozen=True)
class WorkforceIntegrityReport:
    version: str
    status: str
    findings: tuple[IntegrityFinding, ...]
    report_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {"version": self.version, "status": self.status,
                "findings": [f.__dict__ for f in self.findings]}

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()


class WorkforceIntegrityCertification:
    VERSION = "phase7.41"
    REQUIRED = (
        "v71_1_agent_identity.py", "v71_2_agent_capability_permissions.py",
        "v71_9_worker_selection.py", "v71_10_durable_workforce_runtime.py",
        "v71_29_reputation_aware_worker_selection.py",
        "v71_42_coordinated_worker_progress_synchronization.py",
        "v71_43_coordinated_result_verification_handoff.py",
        "v71_44_coordinated_verification_outcome_closure.py",
        "v71_45_workforce_coordination_feedback.py",
        "v71_46_workforce_adaptive_planning.py",
        "v71_47_workforce_coordination_policy_governance.py",
        "v71_48_workforce_coordination_policy_enforcement.py",
        "v71_49_workforce_coordination_recovery.py",
    )
    FORBIDDEN_IMPORT_ROOTS = {"requests", "httpx", "boto3", "openai", "google"}
    FORBIDDEN_MARKERS = ("can_authorize=True", "can_execute=True", "can_call_provider=True",
                         "can_invoke_action_kernel=True")

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _finding(self, control: str, status: str, detail: str) -> IntegrityFinding:
        return IntegrityFinding(control, status, detail)

    def run(self) -> WorkforceIntegrityReport:
        findings: list[IntegrityFinding] = []
        services = self.root / "services"
        missing = [name for name in self.REQUIRED if not (services / name).exists()]
        findings.append(self._finding("required_chain", "PASS" if not missing else "FAIL",
                                      "all canonical workforce chain modules present" if not missing else f"missing: {missing}"))

        forbidden_imports: list[str] = []
        authority_violations: list[str] = []
        for name in self.REQUIRED:
            path = services / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(text)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name.split(".")[0] in self.FORBIDDEN_IMPORT_ROOTS:
                                forbidden_imports.append(f"{name}:{alias.name}")
                    elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in self.FORBIDDEN_IMPORT_ROOTS:
                        forbidden_imports.append(f"{name}:{node.module}")
            except SyntaxError as exc:
                authority_violations.append(f"{name}:syntax:{exc}")
            for marker in self.FORBIDDEN_MARKERS:
                if marker in text:
                    authority_violations.append(f"{name}:{marker}")

        findings.append(self._finding("external_side_effect_boundary", "PASS" if not forbidden_imports else "FAIL",
                                      "no direct external-provider client imports" if not forbidden_imports else str(forbidden_imports)))
        findings.append(self._finding("authority_boundary", "PASS" if not authority_violations else "FAIL",
                                      "no workforce module grants authorization/execution/provider/Kernel authority" if not authority_violations else str(authority_violations)))

        required_terms = {
            "selection": ("capability", "workspace", "capacity"),
            "coordination": ("dependency", "workspace", "fingerprint"),
            "verification": ("verification", "evidence", "workspace"),
            "governance": ("ACTIVE", "fingerprint", "workspace"),
            "recovery": ("RECONCILIATION_REQUIRED", "RECOVERED", "tamper"),
        }
        targets = {
            "selection": services / "v71_9_worker_selection.py",
            "coordination": services / "v71_37_workforce_multi_worker_planning.py",
            "verification": services / "v71_44_coordinated_verification_outcome_closure.py",
            "governance": services / "v71_47_workforce_coordination_policy_governance.py",
            "recovery": services / "v71_49_workforce_coordination_policy_recovery.py",
        }
        # Recovery filename differs in the canonical implementation; resolve it.
        targets["recovery"] = services / "v71_49_workforce_coordination_recovery.py"
        missing_terms = []
        for key, terms in required_terms.items():
            path = targets[key]
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            for term in terms:
                if term.lower() not in text.lower():
                    missing_terms.append(f"{key}:{term}")
        findings.append(self._finding("chain_control_evidence", "PASS" if not missing_terms else "FAIL",
                                      "selection→coordination→verification→governance→recovery controls are source-evidenced" if not missing_terms else str(missing_terms)))

        # Offline scope: architecture/integrity only; production behavior remains separately proven.
        for control, detail in (
            ("live_provider_side_effects", "requires live staging/provider evidence"),
            ("production_crash_recovery", "requires real deployment crash/restart evidence"),
            ("concurrent_database_races", "requires live concurrent database testing"),
            ("credential_revocation", "requires live connector credentials"),
        ):
            findings.append(self._finding(control, "NOT_PROVEN", detail))

        status = "FAIL" if any(f.status == "FAIL" for f in findings) else "CERTIFIED_ARCHITECTURE_INTEGRITY"
        report = WorkforceIntegrityReport(self.VERSION, status, tuple(findings))
        return WorkforceIntegrityReport(**{**report.__dict__, "report_fingerprint": report.fingerprint()})

    @staticmethod
    def assert_integrity(report: WorkforceIntegrityReport) -> None:
        if report.report_fingerprint != report.fingerprint():
            raise ValueError("tampered_report")

    @staticmethod
    def context(report: WorkforceIntegrityReport) -> dict[str, Any]:
        WorkforceIntegrityCertification.assert_integrity(report)
        return {"version": report.version, "status": report.status,
                "report_fingerprint": report.report_fingerprint, "read_only": True,
                "authority": "none", "can_authorize": False, "can_execute": False,
                "can_call_provider": False, "can_invoke_action_kernel": False}
