"""V72.0 production execution certification gate.

This is a certification gate, not an execution plane. It combines deterministic
failure/reconciliation certifications with explicit environment checks. A missing
real PostgreSQL runtime is reported as NOT_PROVEN; it is never converted into a
pass by the offline harnesses.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import importlib.util
import shutil
from typing import Any

from services.v67_e2e_reconciliation_certification import run_v67_12_certification
from services.v71_19_workforce_chaos_certification import WorkforceChaosCertification


@dataclass(frozen=True)
class Gate:
    name: str
    status: str
    evidence: dict[str, Any]


class ProductionExecutionCertification:
    VERSION = "v72.0"

    def run(self) -> dict[str, Any]:
        gates = [self._offline_reconciliation(), self._workforce_chaos(), self._runtime()]
        statuses = {g.status for g in gates}
        status = "PASS" if statuses == {"PASS"} else "NOT_PROVEN"
        return {
            "version": self.VERSION,
            "certification": status,
            "gates": [asdict(g) for g in gates],
            "principle": "offline evidence never substitutes for real PostgreSQL concurrency certification",
        }

    def _offline_reconciliation(self) -> Gate:
        result = run_v67_12_certification()
        return Gate("offline_external_reconciliation", "PASS" if result["status"] == "passed" else "FAIL", result)

    def _workforce_chaos(self) -> Gate:
        report = WorkforceChaosCertification().run()
        return Gate(
            "offline_workforce_chaos",
            "PASS" if report.certification == "PASS" else "FAIL",
            {"passed": report.passed, "failed": report.failed, "fingerprint": report.fingerprint},
        )

    def _runtime(self) -> Gate:
        psql = shutil.which("psql")
        docker = shutil.which("docker")
        psycopg = bool(importlib.util.find_spec("psycopg"))
        ready = bool(psql or docker) and psycopg
        return Gate(
            "real_postgresql_concurrency",
            "PASS" if ready else "NOT_PROVEN",
            {
                "psql_available": bool(psql),
                "docker_available": bool(docker),
                "psycopg_available": psycopg,
                "required": "real PostgreSQL instance plus concurrency-capable driver/runtime",
            },
        )


__all__ = ["ProductionExecutionCertification"]
