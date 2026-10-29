"""Meta Engine surfaces readiness without turning capability gaps into liveness claims."""

import asyncio
from services.meta_engine import MetaEngineV7


class PartialCapabilityEngine:
    def check_health(self):
        return {
            "status": "healthy",
            "readiness": {
                "analysis_ready": True,
                "execution_ready": False,
                "persistence_ready": False,
                "executor_binding": "unavailable",
            },
        }


def test_meta_health_reports_engine_capability_readiness_separately():
    meta = MetaEngineV7()
    try:
        meta.register("condition_engine", PartialCapabilityEngine())
        report = meta.check_health()
        readiness = report["engine_readiness"]["condition_engine"]
        assert readiness["analysis_ready"] is True
        assert readiness["execution_ready"] is False
        assert readiness["executor_binding"] == "unavailable"
    finally:
        asyncio.run(meta.stop())


def test_lifecycle_records_governed_executor_binding_outcome():
    from pathlib import Path
    source = Path("lifecycle.py").read_text(encoding="utf-8")
    section = source[source.index("async def _boot_condition_engine"):source.index("async def _boot_autonomous_content")]
    assert 'set_executor_binding_status("unverified")' in section
    assert 'set_executor_binding_status("unavailable")' in section
    assert 'set_executor_binding_status("bound" if readiness.get("execution_ready") else "unavailable")' in section
