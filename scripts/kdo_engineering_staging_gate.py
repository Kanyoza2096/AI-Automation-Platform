#!/usr/bin/env python3
"""Safe staging gate. Defaults to config-only; live mode is read-only schema probe."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow invocation as `python scripts/kdo_engineering_staging_gate.py` from any cwd.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.kdo_engineering_release_gate import evaluate_staging_configuration
from services.kdo_engineering_staging import preflight_staging_receipt_schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-read-only", action="store_true",
                        help="query receipt schema with configured server-side Supabase client; no writes")
    args = parser.parse_args()
    report = evaluate_staging_configuration()
    if args.live_read_only:
        if not report["ready"]:
            report["live_preflight"] = {"ready": False, "blockers": ["configuration_gate_failed"]}
        else:
            try:
                from services.supabase import get_anon_client_from_settings
                # Use the RLS-aware anon client for the initial read-only probe;
                # never use the service-role key to claim tenant isolation.
                db = get_anon_client_from_settings()
                report["live_preflight"] = preflight_staging_receipt_schema(db)
            except Exception as exc:
                report["live_preflight"] = {
                    "ready": False,
                    "read_only": True,
                    "blockers": ["database_client_initialization_failed"],
                    "error_type": type(exc).__name__,
                }
        report["ready"] = bool(report["ready"] and report["live_preflight"].get("ready"))
        report["mode"] = "live_read_only_schema_preflight"
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
