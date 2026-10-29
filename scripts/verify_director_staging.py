#!/usr/bin/env python3
"""Run a side-effect-free local Director staging smoke check.

This verifies in-process contracts only. It does not connect to production,
Supabase, Redis, or external adapters and does not certify live readiness.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Allow direct invocation from any working directory.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.director import Director
from services.director_bindings import bind_director_capabilities


def main() -> int:
    director = Director(supabase_client=None)
    bindings = bind_director_capabilities(director, container=None)
    catalog = director.capability_catalog()
    diagnostic = asyncio.run(director.execute_capability("self_diagnostic", {}))
    proposal_probe = asyncio.run(director.execute_capability("scale_worker_pool", {
        "pool_name": "p26-staging-probe", "target_workers": 2,
    }))

    checks = {
        "capability_catalog_nonempty": bool(catalog),
        "self_diagnostic_bound": bindings.get("self_diagnostic") == "bound",
        "safe_diagnostic_executed": diagnostic.get("ok") is True,
        "proposal_direct_path_governed": (
            proposal_probe.get("status") == "governance_required"
        ),
        "live_dependencies_verified": False,
        "production_actions_invoked": False,
    }
    report = {
        "phase": "P26",
        "scope": "local_in_process_staging_smoke_check",
        "checks": checks,
        "passed": sum(value is True for key, value in checks.items()
                       if key not in {"live_dependencies_verified", "production_actions_invoked"}),
        "failed": [key for key, value in checks.items()
                   if key not in {"live_dependencies_verified", "production_actions_invoked"} and value is not True],
        "limitations": [
            "No live service adapters or external dependencies were contacted.",
            "Passing checks do not establish production or end-to-end readiness.",
        ],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
