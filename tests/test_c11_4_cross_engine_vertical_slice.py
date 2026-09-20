from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.c11_4_cross_engine_vertical_slice_certification import CrossEngineVerticalSliceCertification
from services.condition_engine import AutonomousConditionEngine, Observation, EngineConfig
from services.director import Director


def test_c11_4_repository_convergence_certification():
    cert = CrossEngineVerticalSliceCertification(__file__.split("/backend/tests/")[0])
    report = cert.run()
    assert report["status"] == "REVIEW"
    assert report["production_certified"] is False
    assert report["blocking_failures"] == 0
    names = {item["name"] for item in report["findings"]}
    assert {"condition_produces_decisions", "director_publish_is_governed", "kernel_is_action_boundary", "canonical_publish_exists"} <= names


def test_condition_engine_turns_business_signal_into_action_plan():
    engine = AutonomousConditionEngine(supabase_client=None, event_bus=MagicMock(), config=EngineConfig(minimum_confidence_for_action=0.20))
    first = Observation(post_id="p1", brand_id="brand-1", likes=10, comments=1, shares=1, clicks=1, impressions=100, topic="AI", metadata={"workspace_id": "ws-1"})
    second = Observation(post_id="p2", brand_id="brand-1", likes=40, comments=5, shares=5, clicks=5, impressions=100, topic="AI", metadata={"workspace_id": "ws-1"})
    engine.observe(first)
    result = engine.observe(second)
    decision = result["decision"]
    assert decision["brand_id"] == "brand-1"
    assert decision["evidence"]["workspace_id"] == "ws-1"
    assert decision["recommended_action"] is not None
    plan = engine.create_action_plan(engine.get_decision(decision["id"]))
    assert plan is not None
    assert plan.decision_id == decision["id"]
    assert plan.parameters["workspace_id"] == "ws-1"


@pytest.mark.asyncio
async def test_director_content_mutation_enters_action_kernel_boundary():
    director = Director(supabase_client=MagicMock())
    container = MagicMock()
    container.supabase_service.client = MagicMock()

    fake_kernel = MagicMock()
    fake_kernel.execute = AsyncMock(return_value={"ok": True, "status": "succeeded", "action_type": "content_adjust_mix"})
    with patch("services.action_kernel.build_default_action_kernel", return_value=fake_kernel):
        from services.director_bindings import bind_director_capabilities
        bind_director_capabilities(director, container)
        capability = director.capabilities["optimize_content_schedule"]
        result = await capability.handler({"workspace_id": "ws-1", "brand_id": "brand-1", "content_mix_change": 0.2})

    assert result["ok"] is True
    fake_kernel.execute.assert_awaited_once()
    call = fake_kernel.execute.await_args.kwargs
    assert call["action_type"] == "content_adjust_mix"
    assert call["workspace_id"] == "ws-1"
    assert call["parameters"]["brand_id"] == "brand-1"


def test_cross_engine_slice_does_not_claim_live_evidence():
    report = CrossEngineVerticalSliceCertification(__file__.split("/backend/tests/")[0]).run()
    by_name = {item["name"]: item for item in report["findings"]}
    assert by_name["live_provider_execution"]["status"] == "NOT_PROVEN"
    assert by_name["real_postgresql_concurrency"]["status"] == "NOT_PROVEN"
    assert by_name["cross_engine_production_e2e"]["status"] == "NOT_PROVEN"
