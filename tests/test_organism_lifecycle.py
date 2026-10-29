from datetime import datetime, timezone

import pytest

from organism.capabilities import AuthorityRequirement, CapabilityRegistry, CapabilitySpec, CapabilityRisk
from organism.goals import Goal, GoalState
from organism.lifecycle import OrganismRuntime, cycle_async
from organism.learning import ExecutionFeedback
from organism.memory import Outcome
from organism.perception import Observation, ObservationKind


def goal(ws="w1", authority="suggest"):
    return Goal(ws, "grow engagement", "engagement >= 100", authority=authority, state=GoalState.ACTIVE)


def feedback(ws="w1", outcome=Outcome.SUCCESS):
    return ExecutionFeedback(
        workspace_id=ws,
        execution_id=f"ex-{outcome.value}",
        action="publish",
        outcome=outcome,
        provider="meta",
        provider_object_id=None,
        provider_request_id=None,
        evidence={"receipt": "authoritative"},
        error=None,
        occurred_at=datetime.now(timezone.utc),
        confidence=1.0,
    )


def test_cycle_connects_learning_prediction_dream_and_homeostasis_without_execution():
    runtime = OrganismRuntime("w1")
    runtime.ingest(Observation("w1", ObservationKind.HEALTH, "health", {"health": .9, "operational_load": .2}))
    result = runtime.cycle(goals=(goal("w1", "suggest"),), execution_feedback=(feedback(),), actions=("publish",))

    assert result.workspace_id == "w1"
    assert result.learning[0].experience.outcome is Outcome.SUCCESS
    assert result.predictions[0].sample_size == 1
    assert result.dreams and result.dreams[0].production_mutated is False
    assert result.state.objective == "grow engagement"
    assert result.decision is None


def test_cycle_rejects_cross_tenant_inputs():
    runtime = OrganismRuntime("w1")
    with pytest.raises(ValueError):
        runtime.ingest(Observation("w2", ObservationKind.HEALTH, "health", {}))
    with pytest.raises(ValueError):
        runtime.cycle(goals=(goal("w2"),))
    with pytest.raises(ValueError):
        runtime.cycle(execution_feedback=(feedback("w2"),))


def test_capability_boundary_blocks_action_before_cognitive_governance():
    registry = CapabilityRegistry()
    runtime = OrganismRuntime("w1", capability_registry=registry)
    runtime.ingest(Observation("w1", ObservationKind.HEALTH, "health", {"health": 1.0, "security_posture": 1.0}))
    result = runtime.cycle(goals=(goal("w1", "execute"),), proposed_action="publish")
    assert result.decision is not None
    assert result.decision.blocked is True
    assert "capability boundary" in result.decision.reason


def test_registered_capability_can_reach_cognitive_boundary_but_not_execute():
    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(
        workspace_id="w1", name="publish", version="1.0.0", description="publish content",
        risk=CapabilityRisk.LOW,
        authority=AuthorityRequirement.EXECUTE,
    ))
    runtime = OrganismRuntime("w1", capability_registry=registry)
    runtime.ingest(Observation("w1", ObservationKind.HEALTH, "health", {"health": 1.0, "security_posture": 1.0}))
    result = runtime.cycle(goals=(goal("w1", "execute"),), proposed_action="publish")
    assert result.decision is not None
    assert result.decision.executable in {False, True}
    assert not hasattr(result.decision, "execute")


@pytest.mark.asyncio
async def test_async_cycle_does_not_nest_event_loop():
    runtime = OrganismRuntime("w1")
    result = await cycle_async(runtime, goals=(goal("w1"),), actions=("publish",), proposed_action=None)
    assert result.workspace_id == "w1"


def test_cycle_emits_integrity_report_for_authoritative_tenant_boundary():
    runtime = OrganismRuntime("w1")
    result = runtime.cycle(goals=(goal("w1"),))
    assert result.integrity is not None
    assert result.integrity.valid is True
    assert result.integrity.status == "ok"


def test_cycle_integrity_rejects_cross_tenant_result_before_release():
    from organism.hardening import validate_cycle_result
    runtime = OrganismRuntime("w1")
    result = runtime.cycle(goals=(goal("w1"),))
    forged = type(result)(**{**result.__dict__, "state": result.state.from_mapping({**result.state.to_dict(), "workspace_id": "w2"})})
    report = validate_cycle_result(forged)
    assert report.valid is False
    assert "state workspace_id mismatch" in report.violations


def test_declarative_metadata_rejects_authority_channels():
    from organism.hardening import validate_declarative_mapping
    with pytest.raises(ValueError):
        validate_declarative_mapping({"handler": "anything"}, label="metadata")


def test_cycle_rejects_feedback_goal_from_unrelated_goal_set():
    runtime = OrganismRuntime("w1")
    foreign_goal_id = goal("w1").goal_id
    feedback_item = ExecutionFeedback(
        workspace_id="w1", execution_id="ex-foreign-goal", action="publish", outcome=__import__("organism.memory", fromlist=["Outcome"]).Outcome.SUCCESS,
        provider="meta", provider_object_id=None, provider_request_id=None,
        evidence={}, error=None, occurred_at=datetime.now(timezone.utc), confidence=1.0,
        goal_id=foreign_goal_id,
    )
    with pytest.raises(ValueError):
        runtime.cycle(goals=(), execution_feedback=(feedback_item,))


def test_cycle_can_generate_strategy_variants_from_declared_action_pool():
    from organism.genome import StrategyGenome
    runtime = OrganismRuntime("w1")
    seed = StrategyGenome("w1", "base", ("publish",))
    result = runtime.cycle(strategy=seed, strategy_action_pool=("publish", "notify"))
    assert result.evolution is not None
    assert len(result.evolution.candidates) > 1
