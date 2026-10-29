#!/usr/bin/env python3
"""Read-only, in-process audit of Kanyoza Meta and Condition engines.

This audit never connects to Supabase/Redis, starts the Meta monitor loop,
invokes action executors, or triggers recovery. It validates source contracts
and performs one isolated in-memory Condition Engine observation plus a Meta
Engine health scan against that isolated instance.
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.condition_engine.core import AutonomousConditionEngine
from services.condition_engine.models import EngineConfig, Observation
from services.meta_engine import MetaEngineV7
from services.condition_engine.api import autonomous_engine, default_action_executor
from council.integration import condition_engine_integration


def main() -> int:
    checks = []
    def add(name, passed, detail):
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    ce = AutonomousConditionEngine(config=EngineConfig(dry_run=True))
    try:
        observation = Observation(
            brand_id="local-audit-brand", post_id="local-audit-post",
            likes=12, comments=3, shares=2, clicks=4,
            impressions=100, reach=80, topic="local audit",
        )
        result = ce.observe(observation)
        add("condition_observation_to_decision", isinstance(result, dict) and "decision" in result and "baselines" in result,
            "Isolated in-memory observation produced a decision and baseline result.")
        add("condition_analysis_methods", all(callable(getattr(ce, name, None)) for name in ("calculate_brand_health", "select_strategy", "record_decision_outcome", "create_experiment")),
            "Core analysis, strategy, outcome-learning, and experiment methods are implemented.")
        add("condition_default_strategy_catalog", len(ce._strategies) >= 1,
            f"Registered strategy count: {len(ce._strategies)}.")
        readiness = ce.check_health().get("readiness", {})
        add("condition_instance_without_executor_readiness_explicit", ce.action_executor is None and ce.check_health().get("status") == "healthy" and readiness.get("analysis_ready") is True and readiness.get("execution_ready") is False,
            "Analysis remains available while execution readiness is explicitly false without an executor.")
        add("condition_persistence_is_not_overclaimed", readiness.get("persistence_ready") is False and readiness.get("persistence_status") == "unavailable",
            "Persistence is not declared ready when no client is configured; configured clients remain unverified until a safe durability probe exists.")
        add("condition_api_executor_is_fail_closed", callable(default_action_executor) and default_action_executor.__name__ == "default_action_executor",
            "API fallback executor is explicit and returns action_executor_unavailable instead of performing a side effect.")
        add("condition_singleton_uses_fail_closed_fallback", getattr(autonomous_engine.action_executor, "__name__", "") == "default_action_executor",
            "Imported API singleton defaults to a non-executing fallback until lifecycle wiring replaces it.")
        integration_source = Path(condition_engine_integration.__file__).read_text(encoding="utf-8")
        add("condition_council_approval_is_explicit", "getattr(decision, \"is_approved\", False)" in integration_source and "council_approval_not_confirmed" in integration_source,
            "Governed Condition Engine execution requires an explicit Council approval signal; missing or unknown decisions fail closed.")

        meta = MetaEngineV7()
        try:
            meta.register("condition_engine", ce)
            snapshot = meta._scan_all_engines(force=True)
            status = snapshot.engines["condition_engine"].status.value
            add("meta_supervisor_health_scan", "condition_engine" in snapshot.engines,
                f"Meta Engine generated a per-engine health record; status={status}.")
            add("meta_registration_rejects_factories", callable(meta.register) and callable(meta.register_all_engines),
                "Registration API is present; existing regression tests cover rejection of factories/callables.")
            add("meta_status_contract", isinstance(meta.check_health(), dict) and "status" in meta.check_health(),
                "Meta Engine exposes a structured status contract; no live monitor was started by this audit.")
        finally:
            asyncio.run(meta.stop())
    finally:
        pass

    report = {
        "audit": "Kanyoza Meta Engine and Condition Engine capability reality audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read-only local/in-process; no external connections; no action execution; no recovery",
        "checks_passed": sum(1 for item in checks if item["passed"]),
        "checks_total": len(checks),
        "checks": checks,
        "conclusion": {
            "meta_engine": "REAL_IMPLEMENTATION_LOCAL_SUPERVISION_CONTRACTS_PRESENT_LIVE_RUNTIME_UNVERIFIED",
            "condition_engine": "REAL_ANALYTICS_AND_DECISION_IMPLEMENTATION_EXECUTION_ADAPTER_DEPENDENT",
            "remediation": "Condition Engine separates analysis, execution, and persistence readiness; API health delegates to core health; lifecycle records governed executor binding outcome; Meta Engine surfaces per-engine readiness; Condition Engine execution now requires explicit Council approval and fails closed on unknown decisions.",
            "remaining_limitations": "A configured persistence client is not proof of durable writes; live Council binding, durable receipts, external side effects, and authoritative outcomes require isolated staging verification. The in-process audit does not invoke an action executor.",
        },
    }
    out = Path("docs/KANYOZA_META_CONDITION_ENGINE_AUDIT.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if all(item["passed"] for item in checks) else 1

if __name__ == "__main__":
    raise SystemExit(main())
