"""Safety contracts for read-only OODA and Meta collaboration evidence."""
import sys
import types
from types import SimpleNamespace

import pytest

from api.engine_handoff_evidence import load_meta_evidence, load_ooda_evidence


@pytest.mark.asyncio
async def test_ooda_handoff_reads_status_without_starting_or_stepping_loop(monkeypatch):
    calls = []

    class Loop:
        _running = True
        _current_phase = "ORIENT"

        def get_status(self):
            calls.append("status")
            return {"cycles": 4, "phase": "ORIENT"}

        async def start(self):
            calls.append("start")

        async def step(self):
            calls.append("step")

    fake = types.ModuleType("core.autonomous_loop")
    fake._autonomous_loop = Loop()
    monkeypatch.setitem(sys.modules, "core.autonomous_loop", fake)

    evidence = await load_ooda_evidence()

    assert evidence["status"] == "available"
    assert evidence["phase"] == "ORIENT"
    assert evidence["execution_authority"] == "none"
    assert calls == ["status"]


@pytest.mark.asyncio
async def test_meta_handoff_refreshes_diagnoses_without_invoking_recovery(monkeypatch):
    calls = []

    class Meta:
        async def check_health_async(self):
            calls.append("refresh")

        def check_health(self):
            calls.append("health")
            return {
                "available": True,
                "status": "degraded",
                "health_score": 0.7,
                "risk_score": 0.2,
                "engine_observation_contract": {
                    "contract": "meta_engine.observations/v1",
                    "status": "partial",
                    "source_count": 1,
                    "accepted_count": 1,
                    "rejected_count": 0,
                    "freshness_seconds": 60,
                },
                "engine_observations": [
                    {"service_id": "worker-a", "status": "degraded", "observed_at": "2026-01-01T00:00:00Z"}
                ],
            }

        def diagnose(self):
            calls.append("diagnose")
            return [SimpleNamespace(
                id="diag-1", engine="worker-a", symptom="latency",
                root_cause="unknown", severity="warning",
                recommended_action="inspect queue", confidence=0.6,
            )]

        def _start_recovery(self, _diagnosis):
            calls.append("recovery")
            raise AssertionError("Meta handoff must never start recovery")

    fake = types.ModuleType("services.meta_engine")
    fake.meta_engine = Meta()
    monkeypatch.setitem(sys.modules, "services.meta_engine", fake)

    evidence = await load_meta_evidence()

    assert evidence["status"] == "available"
    assert evidence["observation_contract"]["status"] == "partial"
    assert evidence["diagnoses"][0]["id"] == "diag-1"
    assert evidence["diagnoses"][0]["independently_verified"] is False
    assert evidence["recovery_started"] is False
    assert evidence["execution_authority"] == "none"
    assert calls == ["refresh", "health", "diagnose"]


@pytest.mark.asyncio
async def test_meta_handoff_fails_closed_when_health_provider_is_unavailable(monkeypatch):
    fake = types.ModuleType("services.meta_engine")
    fake.meta_engine = object()
    monkeypatch.setitem(sys.modules, "services.meta_engine", fake)

    evidence = await load_meta_evidence()

    assert evidence["status"] == "unavailable"
    assert evidence["execution_authority"] == "none"
    assert evidence["recovery_started"] is False

def test_collaboration_run_id_accepts_uuid_correlation_and_rejects_invalid_values():
    import uuid
    from fastapi import HTTPException
    from api.routers.ai_chat import _resolve_collaboration_run_id

    requested = str(uuid.uuid4())
    assert _resolve_collaboration_run_id(requested) == requested
    assert uuid.UUID(_resolve_collaboration_run_id()).version == 4
    for invalid in ("not-a-uuid", 123, "x" * 65):
        with pytest.raises(HTTPException) as error:
            _resolve_collaboration_run_id(invalid)
        assert error.value.status_code == 422
