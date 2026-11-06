"""Condition Engine event envelopes preserve tenant and causal execution context."""
from types import SimpleNamespace

import pytest

from services.condition_engine import utils as condition_utils
from services.condition_engine.core import AutonomousConditionEngine


@pytest.mark.asyncio
async def test_condition_event_publisher_preserves_workspace_and_causal_metadata(monkeypatch):
    captured = {}

    class FakeBus:
        async def publish(self, event_name, payload, **kwargs):
            captured["event_name"] = event_name
            captured["payload"] = payload
            captured["kwargs"] = kwargs
            return "event-test"

    async def get_bus():
        return FakeBus()

    monkeypatch.setattr(condition_utils, "HAS_EVENT_BUS", True)
    monkeypatch.setattr(condition_utils, "get_unified_bus", get_bus)

    payload = {
        "workspace_id": "workspace-test",
        "brand_id": "brand-test",
        "decision_id": "decision-test",
        "correlation_id": "correlation-test",
        "execution_id": "execution-test",
        "intent_id": "intent-test",
        "parent_event_id": "parent-event-test",
    }
    await condition_utils._publish_event_async("strategy_executed", payload)

    assert captured["event_name"] == "strategy_executed"
    assert captured["payload"]["workspace_id"] == "workspace-test"
    assert captured["kwargs"] == {
        "source": "condition_engine",
        "workspace_id": "workspace-test",
        "correlation_id": "correlation-test",
        "parent_event_id": "parent-event-test",
        "brand_id": "brand-test",
        "decision_id": "decision-test",
        "execution_id": "execution-test",
        "intent_id": "intent-test",
    }


def test_action_parameters_carry_decision_correlation_id():
    decision = SimpleNamespace(
        recommended_action="increase_similar_content",
        evidence={"workspace_id": "workspace-test"},
        correlation_id="correlation-test",
    )
    parameters = AutonomousConditionEngine._build_action_parameters(None, decision)

    assert parameters["workspace_id"] == "workspace-test"
    assert parameters["correlation_id"] == "correlation-test"
