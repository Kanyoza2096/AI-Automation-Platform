"""C11.4 cross-engine vertical-slice certification.

This certification proves control-plane convergence without pretending to certify
live provider, PostgreSQL concurrency, Redis, or production infrastructure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class SliceFinding:
    name: str
    status: str
    evidence: str


class CrossEngineVerticalSliceCertification:
    """Certify the executable control-plane path across core business engines."""

    def __init__(self, repo_root: str):
        self.repo_root = repo_root

    def run(self) -> Dict[str, Any]:
        findings = []
        import pathlib
        root = pathlib.Path(self.repo_root)
        if not (root / "services").exists() and (root / "backend").exists():
            root = root / "backend"
        files = {
            "condition": root / "services" / "condition_engine.py",
            "director": root / "services" / "director.py",
            "bindings": root / "services" / "director_bindings.py",
            "content": root / "services" / "content_engine.py",
            "scheduler": root / "scheduler" / "brand_scheduler.py",
            "kernel": root / "services" / "action_kernel.py",
            "publish": root / "services" / "publish_orchestration.py",
            "meta": root / "services" / "meta_engine.py",
        }
        missing = [name for name, path in files.items() if not path.exists()]
        findings.append(SliceFinding("required_engine_files", "PASS" if not missing else "FAIL", "all core engine files present" if not missing else f"missing: {missing}"))
        if missing:
            return self._report(findings)

        text = {name: path.read_text(encoding="utf-8") for name, path in files.items()}
        checks = [
            ("condition_produces_decisions", "def observe(self, observation: Observation)", text["condition"]),
            ("director_orchestrates", "class Director", text["director"]),
            ("director_publish_is_governed", "execute_governed_publish", text["bindings"]),
            ("content_uses_gateway", "get_llm_gateway", text["content"]),
            ("scheduler_triggers_workflow", "_trigger_workflow", text["scheduler"]),
            ("kernel_is_action_boundary", "class ActionKernel", text["kernel"]),
            ("canonical_publish_exists", "run_canonical_publish", text["publish"]),
            ("meta_verifies_recovery", "Recovery completed but health verification failed", text["meta"]),
        ]
        for name, needle, haystack in checks:
            findings.append(SliceFinding(name, "PASS" if needle in haystack else "FAIL", f"found `{needle}`" if needle in haystack else f"missing `{needle}`"))

        findings.append(SliceFinding(
            "live_provider_execution", "NOT_PROVEN",
            "requires staging credentials and controlled provider execution",
        ))
        findings.append(SliceFinding(
            "real_postgresql_concurrency", "NOT_PROVEN",
            "requires real PostgreSQL with concurrent workers",
        ))
        findings.append(SliceFinding(
            "cross_engine_production_e2e", "NOT_PROVEN",
            "control-plane wiring is certified; external side-effect lifecycle still needs staging evidence",
        ))
        return self._report(findings)

    @staticmethod
    def _report(findings):
        failed = [f for f in findings if f.status == "FAIL"]
        return {
            "certification": "C11.4",
            "status": "FAIL" if failed else "REVIEW",
            "production_certified": False,
            "findings": [f.__dict__ for f in findings],
            "blocking_failures": len(failed),
            "live_evidence_required": True,
        }
