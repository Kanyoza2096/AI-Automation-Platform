"""Regression tests for Director capability binding policy."""

from types import SimpleNamespace
import asyncio

import core.autonomous_content as autonomous_content
from services.director_bindings import _build_handlers_for

from services.director_bindings import (
    INTENTIONALLY_UNBOUND_CAPABILITIES,
    assert_no_unbound_capabilities,
)


def _cap(handler):
    return SimpleNamespace(handler=handler)


def test_scheduled_publishing_is_intentionally_excluded_from_binding_audit():
    director = SimpleNamespace(
        capabilities={
            "publish_scheduled_content": _cap(lambda params: None),
            "review_content_quality": _cap(lambda params: None),
        }
    )

    assert "publish_scheduled_content" in INTENTIONALLY_UNBOUND_CAPABILITIES
    assert assert_no_unbound_capabilities(director) == ["review_content_quality"]


def test_audit_still_reports_unintended_placeholder_handlers():
    def real_handler(params):
        return {"ok": True}

    def placeholder_handler(params):
        return None

    director = SimpleNamespace(
        capabilities={
            "healthy_capability": _cap(real_handler),
            "broken_capability": _cap(placeholder_handler),
        }
    )

    assert assert_no_unbound_capabilities(director) == ["broken_capability"]


def test_review_content_quality_is_bound_but_publishing_is_not():
    handlers = _build_handlers_for(SimpleNamespace(), None)

    assert "review_content_quality" in handlers
    assert "publish_scheduled_content" not in handlers


def test_quality_review_returns_advisory_without_executing_remediation(monkeypatch):
    class Engine:
        _running = True

        def check_health(self):
            return {
                "metrics": {
                    "director_signals": [{
                        "capability": "review_content_quality",
                        "params": {"brand_id": "brand-1"},
                        "rationale": "Three recent measured scores below threshold",
                    }]
                }
            }

    async def get_engine():
        return Engine()

    monkeypatch.setattr(autonomous_content, "get_autonomous_content", get_engine)
    handler = _build_handlers_for(SimpleNamespace(), None)["review_content_quality"]
    result = asyncio.run(handler({"brand_id": "brand-1"}))

    assert result["ok"] is True
    assert result["advisory_only"] is True
    assert result["execution_performed"] is False
    assert result["result"]["advisory"] is True


def test_service_registry_resolves_current_director_singleton(monkeypatch):
    import services.director.api as director_api
    from core.service_definitions import _resolve_director

    live_director = object()
    monkeypatch.setattr(director_api, "director", live_director)

    assert _resolve_director(None) is live_director


def test_director_does_not_register_or_accept_brand_scheduler_publishing():
    from services.director import Director
    from services.director.models import Health, Observation, Sector
    from datetime import datetime, timezone

    director = Director(supabase_client=None)
    assert "publish_scheduled_content" not in director.capabilities

    director.observations = {
        "brand-engine": Observation(
            source="brand-engine",
            sector=Sector.CONTENT,
            status=Health.NOMINAL,
            timestamp=datetime.now(timezone.utc),
            metrics={
                "director_signal_contract": "metrics.director_signals/v1",
                "director_signals": [{
                    "capability": "publish_scheduled_content",
                    "description": "Publish the next scheduled brand post",
                    "params": {"brand_id": "brand-1"},
                    "confidence": 0.99,
                }],
            },
        )
    }

    result = director._engine_decision_signals()
    assert result["accepted"] == []
    assert result["rejected"] == [{
        "observer": "brand-engine",
        "index": 0,
        "reason": "unknown_capability",
    }]
