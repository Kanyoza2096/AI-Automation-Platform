from types import SimpleNamespace

import pytest

from services.director.health_briefing import (
    generate_health_briefing,
    render_health_briefing,
)


class FakeGateway:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def complete_async_structured(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return self.result


@pytest.mark.asyncio
async def test_health_briefing_uses_director_gateway_and_filters_unknown_evidence_refs():
    gateway = FakeGateway(SimpleNamespace(
        ok=True,
        data={
            "executive_assessment": "Job processing needs investigation; cause is undetermined.",
            "incidents": [{
                "service": "job_service",
                "assessment": "Observed degraded state; cause undetermined.",
                "impact": "Impact is not established.",
                "confidence": "low",
                "evidence_refs": ["health:job_service", "invented:reference"],
                "next_step": "Inspect persisted failed jobs and queue age.",
            }, {
                "service": "not_a_real_service",
                "assessment": "Invented service",
                "impact": "Unknown",
                "confidence": "low",
                "evidence_refs": [],
                "next_step": "Investigate",
            }],
            "trends": [],
            "limitations": ["No historical comparison supplied."],
        },
        provider="test-provider",
    ))

    brief = await generate_health_briefing(
        {"job_service": "unknown", "director": "ok"},
        diagnostics={"job_stats_service": {
            "provider_reported_status": "degraded",
            "status_counts": {"FAILED": 4, "STALE": 2},
            "failure_intelligence": {"status": "available", "categories": {
                "voice_compliance": {"count": 2, "recommended_action": "bounded_voice_rewrite"}
            }},
        }},
        gateway=gateway,
    )

    assert "voice_compliance" in gateway.calls[0][0]
    assert '"FAILED": 4' in gateway.calls[0][0]
    assert brief["mode"] == "llm"
    assert brief["provider"] == "test-provider"
    assert len(brief["incidents"]) == 1
    assert brief["incidents"][0]["evidence_refs"] == ["health:job_service"]
    assert gateway.calls[0][1]["caller"] == "director"
    assert gateway.calls[0][1]["workspace_id"] == "platform"


@pytest.mark.asyncio
async def test_health_briefing_fallback_is_explicit_and_does_not_claim_root_cause():
    gateway = FakeGateway(SimpleNamespace(ok=False, reason="provider_unavailable"))
    brief = await generate_health_briefing(
        {"job_service": "down", "director": "ok"},
        gateway=gateway,
    )

    assert brief["mode"] == "deterministic_fallback"
    assert "not a root-cause diagnosis" in brief["executive_assessment"]
    assert brief["incidents"][0]["confidence"] == "undetermined"
    assert "Cause undetermined." in brief["incidents"][0]["assessment"]


def test_render_escapes_untrusted_service_and_model_text():
    brief = {
        "mode": "llm",
        "executive_assessment": "<script>alert(1)</script>",
        "incidents": [{
            "service": "<img>",
            "assessment": "Cause undetermined",
            "impact": "Unknown",
            "confidence": "low",
            "evidence_refs": ["health:job_service"],
            "next_step": "Inspect logs",
        }],
        "trends": [],
        "limitations": ["No historical data"],
    }
    rendered = render_health_briefing(brief, {"<img>": "down"})

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&lt;img&gt;" in rendered
