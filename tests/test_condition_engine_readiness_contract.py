"""Regression tests for explicit Condition Engine capability readiness."""

from services.condition_engine.core import AutonomousConditionEngine
from services.condition_engine.models import EngineConfig


def test_analysis_can_be_ready_while_execution_is_unavailable():
    engine = AutonomousConditionEngine(config=EngineConfig(dry_run=True))
    health = engine.check_health()
    assert health["status"] == "healthy"
    assert health["readiness"]["analysis_ready"] is True
    assert health["readiness"]["execution_ready"] is False
    assert "action_executor_unavailable" in health["readiness"]["limitations"]


def test_callable_executor_is_not_proof_of_governed_binding_or_persistence():
    engine = AutonomousConditionEngine(
        config=EngineConfig(dry_run=True),
        action_executor=lambda plan: {"ok": True},
        supabase_client=object(),
    )
    readiness = engine.readiness_report()
    assert readiness["execution_ready"] is True
    assert readiness["executor_binding"] == "unverified"
    assert readiness["persistence_configured"] is True
    assert readiness["persistence_ready"] is False
    assert readiness["persistence_status"] == "unknown"


def test_binding_status_only_accepts_known_values():
    engine = AutonomousConditionEngine(config=EngineConfig(dry_run=True))
    engine.set_executor_binding_status("bound")
    assert engine.readiness_report()["executor_binding"] == "bound"
    engine.set_executor_binding_status("made_up")
    assert engine.readiness_report()["executor_binding"] == "unverified"


def test_api_health_uses_core_readiness_instead_of_hardcoded_green():
    from services.condition_engine import api

    result = api.health_check()
    assert result["status"] == api.autonomous_engine.check_health()["status"]
    assert result["readiness"]["analysis_ready"] is True
    assert result["readiness"]["execution_ready"] is False
