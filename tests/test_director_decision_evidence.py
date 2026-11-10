"""Structured Director decisions retain evidence, uncertainty and action context."""

import asyncio
from datetime import datetime, timezone

import pytest

from services.director import Director
from services.director.models import (
    Authority,
    Decision,
    Health,
    Observation,
    Risk,
    Sector,
    Task,
    Status,
)


def make_task(*, sources=None):
    return Task(
        id="task-evidence-1",
        sector=Sector.INFRA,
        description="Inspect the event pipeline",
        capability="self_diagnostic",
        authority=Authority.SUGGEST,
        risk=Risk.LOW,
        confidence=0.73,
        reason="A source reported degraded event delivery",
        sources=list(sources or []),
        status=Status.PLANNED,
    )


@pytest.mark.asyncio
async def test_record_decision_captures_source_evidence_and_marks_unverified_rationale():
    director = Director(supabase_client=None)
    director.observations = {
        "event-monitor": Observation(
            source="event-monitor",
            sector=Sector.INFRA,
            status=Health.DEGRADED,
            issues=[],
            timestamp=datetime.now(timezone.utc),
        ),
        "unrelated-observer": Observation(
            source="unrelated-observer",
            sector=Sector.CONTENT,
            status=Health.NOMINAL,
            timestamp=datetime.now(timezone.utc),
        ),
    }

    director._record_decision(
        "Planned: inspect event delivery",
        "A source reported degraded event delivery",
        make_task(sources=["event-monitor"]),
        outcome="planned",
        stage="planning",
    )
    await asyncio.sleep(0)

    decision = director.decisions[-1]
    assert decision.stage == "planning"
    assert decision.evidence_state == "present"
    assert [item["observer"] for item in decision.evidence_sources] == ["event-monitor"]
    assert decision.observed_state[0]["reported_status"] == "degraded"
    assert decision.inferences == [{
        "claim": "A source reported degraded event delivery",
        "classification": "inference",
        "verification": "not_independently_verified",
    }]
    assert decision.assumptions == []
    assert decision.justification == "A source reported degraded event delivery"
    assert decision.action_taken["task_id"] == "task-evidence-1"
    assert decision.confidence_basis["calibration"] == "not_evidence_calibrated"

    persisted = await director.store.list_decisions()
    assert persisted[-1]["id"] == decision.id
    assert persisted[-1]["evidence_sources"][0]["observer"] == "event-monitor"


@pytest.mark.asyncio
async def test_decision_without_observations_is_explicitly_unknown_not_evidence_backed():
    director = Director(supabase_client=None)

    director._record_decision(
        "Planned: inspect event delivery",
        "No current observer evidence was available",
        make_task(),
        outcome="planned",
        stage="planning",
    )
    await asyncio.sleep(0)

    decision = director.decisions[-1]
    assert decision.evidence_state == "unknown"
    assert decision.evidence_sources == []
    assert decision.observed_state == []
    assert decision.assumptions == []
    assert decision.confidence_basis["fresh_evidence_source_count"] == 0


@pytest.mark.asyncio
async def test_decision_audit_is_restored_from_store_after_director_restart():
    original = Director(supabase_client=None)
    decision = Decision(
        id="decision-persisted-1",
        timestamp=datetime.now(timezone.utc),
        decision="Planned: inspect event delivery",
        reason="Fresh event monitor observation",
        confidence=0.8,
        sector=Sector.INFRA,
        authority=Authority.SUGGEST,
        risk=Risk.LOW,
        task_id="task-persisted-1",
        outcome="planned",
        stage="planning",
        observed_state=[{"observer": "event-monitor", "reported_status": "degraded"}],
        inferences=[{"claim": "delivery is degraded", "classification": "inference"}],
        assumptions=[],
        evidence_sources=[{"observer": "event-monitor", "freshness": "fresh"}],
        evidence_state="present",
        justification="Fresh event monitor observation",
        action_taken={"task_id": "task-persisted-1", "stage": "planning"},
        confidence_basis={"calibration": "not_evidence_calibrated"},
    )
    await original.store.save_decision(decision)

    restarted = Director(supabase_client=None)
    restarted.store = original.store
    summary = await restarted._restore_decisions()

    assert summary == {"restored": 1, "errors": 0}
    restored = restarted.decisions[-1]
    assert restored.id == "decision-persisted-1"
    assert restored.sector == Sector.INFRA
    assert restored.stage == "planning"
    assert restored.evidence_sources[0]["observer"] == "event-monitor"
    assert restored.action_taken["task_id"] == "task-persisted-1"


@pytest.mark.asyncio
async def test_legacy_decision_records_restore_with_safe_defaults():
    director = Director(supabase_client=None)
    director.store.decisions.append({
        "id": "decision-legacy-1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decision": "Executed: legacy task",
        "reason": "Legacy reason",
        "confidence": 0.5,
        "sector": "infra",
        "authority": "suggest",
        "risk": "low",
        "task_id": "legacy-task",
        "outcome": "completed",
    })

    summary = await director._restore_decisions()

    assert summary == {"restored": 1, "errors": 0}
    restored = director.decisions[-1]
    assert restored.id == "decision-legacy-1"
    assert restored.evidence_state == "unknown"
    assert restored.evidence_sources == []
    assert restored.assumptions == []
