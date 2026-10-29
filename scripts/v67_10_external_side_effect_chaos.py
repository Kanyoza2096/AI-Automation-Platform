"""V67.10 controlled external-side-effect chaos runner.

Default mode is offline and performs no network calls. --live-sandbox requires
an explicit sandbox provider URL and is intentionally separate from production
connectors. This runner is a certification harness, not a business operation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# Make direct script execution behave like module execution when invoked from
# the backend root or from another working directory.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.execution_ledger import ExecutionLedger
from services.external_action_executor import ExternalActionExecutor
from services.v67_external_side_effect_sandbox import SandboxProvider


@dataclass
class Report:
    status: str
    checks: dict
    error: str = ""


def run_local() -> Report:
    checks = {}
    provider = SandboxProvider()
    ledger = ExecutionLedger(allow_in_memory=True)
    executor = ExternalActionExecutor(ledger)

    async def execute(key: str, mode: str):
        return await executor.execute(
            workspace_id="v67.10-chaos",
            action="sandbox.external_side_effect_probe",
            idempotency_key=key,
            provider="v67.10-sandbox",
            operation=lambda: provider.submit(idempotency_key=key, failure_mode=mode),
        )

    async def scenario():
        success = await execute("runner-success", "success")
        checks["success_receipt"] = success["status"] == "succeeded"

        uncertain = await execute("runner-timeout", "timeout_after_commit")
        checks["commit_then_timeout_is_uncertain"] = uncertain["status"] == "uncertain"
        remote = provider.reconcile(idempotency_key="runner-timeout")
        checks["reconciliation_finds_remote_commit"] = remote["status"] == "committed"

        replay = await execute("runner-timeout", "success")
        checks["provider_idempotency_prevents_duplicate"] = (
            replay.get("replayed") is True and provider.committed_count == 2 and provider.call_count == 2
        )

        rejected = await execute("runner-reject", "reject_before_commit")
        checks["pre_commit_rejection_has_no_side_effect"] = (
            rejected["status"] == "failed" and provider.reconcile(idempotency_key="runner-reject")["status"] == "not_found"
        )
        checks["all_side_effects_accounted_for"] = provider.committed_count == 2

    try:
        asyncio.run(scenario())
        return Report("passed" if all(checks.values()) else "failed", checks)
    except Exception as exc:
        return Report("failed", checks, str(exc))


def run_live_sandbox(url: str) -> Report:
    """Refuse arbitrary endpoints; live integration is deliberately not implicit.

    The current release ships the gate and contract, but no production provider
    credentials or approved external sandbox endpoint. A live adapter should be
    added only after its provider-specific idempotency/reconciliation contract is
    reviewed.
    """
    if not url or not url.startswith("https://"):
        return Report("blocked", {}, "live sandbox requires an explicit HTTPS endpoint")
    return Report(
        "blocked",
        {"endpoint_contract_present": True},
        "No approved provider-specific V67.10 adapter is configured; refusing arbitrary live side effects",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-sandbox", action="store_true")
    parser.add_argument("--sandbox-url", default=os.environ.get("KDO_V67_10_SANDBOX_URL", ""))
    args = parser.parse_args(argv)
    report = run_live_sandbox(args.sandbox_url) if args.live_sandbox else run_local()
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    return 0 if report.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
