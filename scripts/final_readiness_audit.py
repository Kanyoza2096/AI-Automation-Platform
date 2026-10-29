#!/usr/bin/env python3
"""Produce a reproducible, conservative Kanyoza final-readiness audit.

This script runs the bounded Director regression suite and the local staging
smoke check. It deliberately never connects to production services or invokes
side-effecting capabilities. Local success is not a production certification.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOCUSED_TESTS = [
    "tests/test_director*.py",
    "tests/test_engine_truth.py",
    "tests/test_phase3714_health_truth.py",
    "tests/test_backend_route_contract.py",
    "tests/test_phase16_director_signal_publishers.py",
    "tests/test_phase18_evidence_backed_job_signals.py",
    "tests/test_phase19_meta_engine_signals.py",
    "tests/test_phase20_autonomous_content_intelligence.py",
    "tests/test_phase22_director_runtime_binding.py",
    "tests/test_phase23_verified_outcome_unknown.py",
    "tests/test_phase24_evidence_driven_recovery.py",
    "tests/test_phase25_capability_coverage_audit.py",
    "tests/test_phase26_staging_workflow.py",
]


def run(command: list[str]) -> dict:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    return {
        "command": command,
        "returncode": result.returncode,
        "passed": result.returncode == 0,
        "stdout_tail": result.stdout[-6000:],
        "stderr_tail": result.stderr[-3000:],
    }


def main() -> int:
    # Expand only the known test glob, using pytest's own shell-free path list.
    import glob
    test_paths: list[str] = []
    for pattern in FOCUSED_TESTS:
        matches = sorted(glob.glob(str(ROOT / pattern))) if "*" in pattern else [str(ROOT / pattern)]
        test_paths.extend(str(Path(path).relative_to(ROOT)) for path in matches if Path(path).exists())
    test_paths = list(dict.fromkeys(test_paths))

    regression = run([sys.executable, "-m", "pytest", "-q", *test_paths])
    staging = run([sys.executable, "scripts/verify_director_staging.py"])
    compile_check = run([sys.executable, "-m", "compileall", "-q", "services/director", "services/director_bindings.py", "scripts/verify_director_staging.py", "scripts/final_readiness_audit.py"])

    phase_reports = []
    for phase in range(3, 27):
        matches = sorted(ROOT.glob(f"KANYOZA_P{phase}_*REPORT.md"))
        phase_reports.append({"phase": f"P{phase}", "report_found": bool(matches), "reports": [p.name for p in matches]})

    try:
        smoke_payload = json.loads(staging["stdout_tail"])
    except (json.JSONDecodeError, TypeError):
        smoke_payload = None

    checks = {
        "focused_regression_suite": regression["passed"],
        "local_staging_contracts": staging["passed"] and isinstance(smoke_payload, dict) and not smoke_payload.get("failed"),
        "python_compilation": compile_check["passed"],
        "phase_report_inventory_completed": len(phase_reports) == 24,
    }
    report = {
        "audit": "Kanyoza P27 Final Readiness and Closure Audit",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository_root": str(ROOT),
        "scope": "local repository and in-process contracts only",
        "checks": checks,
        "local_checks_passed": sum(value is True for value in checks.values()),
        "local_checks_total": len(checks),
        "focused_regression": regression,
        "staging_smoke_check": staging,
        "staging_smoke_payload": smoke_payload,
        "compilation": compile_check,
        "phase_report_inventory": phase_reports,
        "missing_phase_report_artifacts": [item["phase"] for item in phase_reports if not item["report_found"]],
        "environment": {
            "supabase_python_package_available": importlib.util.find_spec("supabase") is not None,
            "live_supabase_verified": False,
            "live_redis_verified": False,
            "external_adapters_verified": False,
            "cross_service_delivery_verified": False,
            "production_actions_invoked": False,
        },
        "known_full_suite_blocker": {
            "status": "not_green",
            "detail": "Full pytest run is not certified: the first failure in pytest -x was tests/pipeline/test_publish_end_to_end_contract.py::test_connector_receives_brand_for_tenant_specific_facebook_credentials. The canonical pipeline fails closed because the Supabase package/client is unavailable. A non-fail-fast full run also exceeded the execution time budget.",
            "policy": "Do not weaken fail-closed publishing behavior or count missing infrastructure as a passing integration test.",
        },
        "production_readiness": "NOT_VERIFIED",
        "limitations": [
            "Passing local tests do not prove deployed service wiring or dependency health.",
            "No production credentials, live services, external adapters, or side-effecting actions were used.",
            "A controlled staging environment with required dependencies is needed to certify full end-to-end behavior.",
        ],
    }
    output = ROOT / "docs" / "KANYOZA_P27_FINAL_READINESS_AUDIT.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"report_path": str(output), "checks": checks,
                      "local_checks_passed": report["local_checks_passed"],
                      "local_checks_total": report["local_checks_total"],
                      "production_readiness": report["production_readiness"],
                      "known_full_suite_blocker": report["known_full_suite_blocker"]}, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
