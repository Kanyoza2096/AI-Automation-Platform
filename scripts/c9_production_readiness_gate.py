#!/usr/bin/env python3
"""C9.5 production-readiness evidence aggregator.

This gate never upgrades missing external evidence to PASS.  It reports each
control as PASS, NOT_PROVEN, or FAIL and returns non-zero unless every
required control is PASS.  Live infrastructure checks are opt-in through
explicit environment variables; absence is NOT_PROVEN, not failure.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_script(path: str) -> tuple[str, str]:
    p = subprocess.run([sys.executable, str(ROOT / path)], cwd=ROOT, text=True, capture_output=True)
    return ("PASS" if p.returncode == 0 else "FAIL", (p.stdout + p.stderr)[-12000:])


def static_checks() -> list[dict]:
    checks = []
    for name, script in [
        ("release_integrity", "scripts/release_integrity_gate.py"),
        ("security_surface", "scripts/security_surface_check.py"),
        ("tenant_security_offline", "scripts/tenant_security_certification.py"),
    ]:
        status, output = run_script(script)
        checks.append({"name": name, "status": status, "evidence": output})

    required = [
        "scripts/backup_restore_certification.py",
        "scripts/worker_health.py",
        "scripts/c9_production_readiness_gate.py",
    ]
    missing = [x for x in required if not (ROOT / x).is_file()]
    checks.append({"name": "required_certification_artifacts", "status": "FAIL" if missing else "PASS",
                   "evidence": {"missing": missing}})

    version = (ROOT / "platform_version.py").read_text(encoding="utf-8")
    match = re.search(r'^PLATFORM_VERSION\s*=\s*["\']([^"\']+)["\']\s*$', version, re.M)
    checks.append({"name": "canonical_release_identity", "status": "PASS" if match else "FAIL",
                   "evidence": {"version": match.group(1) if match else None}})
    return checks


def external_checks() -> list[dict]:
    # External evidence is accepted only from a machine-readable manifest
    # emitted by a certification runner. There is intentionally no boolean
    # environment-variable escape hatch that can turn NOT_PROVEN into PASS.
    path = os.getenv("KDO_C9_EVIDENCE_MANIFEST", "").strip()
    names = {
        "real_postgresql_concurrency": "requires real PostgreSQL concurrent execution evidence",
        "live_backup_restore": "requires isolated real-database restore and verification",
        "real_provider_execution": "requires controlled non-production provider execution and authoritative receipt",
        "real_redis_recovery": "requires real Redis lease/recovery evidence",
    }
    if not path:
        return [{"name": name, "status": "NOT_PROVEN", "evidence": detail} for name, detail in names.items()]
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        return [{"name": name, "status": "FAIL", "evidence": f"invalid evidence manifest: {type(exc).__name__}: {exc}"} for name in names]
    results = data.get("checks", {}) if isinstance(data, dict) else {}
    checks = []
    for name, detail in names.items():
        item = results.get(name) if isinstance(results, dict) else None
        status = str(item.get("status", "")).upper() if isinstance(item, dict) else ""
        if status == "PASS" and item.get("evidence"):
            checks.append({"name": name, "status": "PASS", "evidence": item["evidence"]})
        elif status == "FAIL":
            checks.append({"name": name, "status": "FAIL", "evidence": item.get("evidence", "external certification failed")})
        else:
            checks.append({"name": name, "status": "NOT_PROVEN", "evidence": detail})
    return checks


def main() -> int:
    checks = static_checks() + external_checks()
    failed = [c for c in checks if c["status"] == "FAIL"]
    unknown = [c for c in checks if c["status"] == "NOT_PROVEN"]
    status = "PASS" if not failed and not unknown else ("BLOCKED" if not failed else "FAIL")
    report = {
        "schema_version": "1.0",
        "certification": "C9.5",
        "status": status,
        "checks": checks,
        "blocking_failures": [c["name"] for c in failed],
        "unproven": [c["name"] for c in unknown],
        "production_certified": status == "PASS",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
