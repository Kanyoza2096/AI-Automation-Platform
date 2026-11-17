"""Read-only strategic context contract for the Director planner."""

from datetime import datetime, timezone

import pytest

from services.director import Director
from services.director.models import (
    Authority,
    Health,
    Observation,
    Risk,
    Sector,
    Status,
    Task,
)


def test_strategic_context_combines_objectives_evidence_capabilities_missions_and_learning():
    director = Director(supabase_client=None)
    director.observations = {
        "content-engine": Observation(
            source="content-engine",
            sector=Sector.CONTENT,
            status=Health.NOMINAL,
            issues=[{"type": "content_gap"}],
            timestamp=datetime.now(timezone.utc),
            metrics={
                "director_signal_contract": "metrics.director_signals/v1",
                "director_signals": [{
                    "capability": "optimize_content_schedule",
                    "description": "Review the publishing schedule gap",
                    "params": {"brand_id": "brand-1", "api_key": "do-not-expose"},
                    "confidence": 0.8,
                }],
            },
        ),
        "unknown-engine": Observation(
            source="unknown-engine",
            sector=Sector.INFRA,
            status=Health.UNKNOWN,
            timestamp=datetime.now(timezone.utc),
        ),
    }
    director.tasks["task-1"] = Task(
        id="task-1",
        sector=Sector.CONTENT,
        description="Review a measured content gap",
        capability="review_content_quality",
        authority=Authority.NOTIFY,
        risk=Risk.LOW,
        params={"api_key": "task-secret"},
        result={"access_token": "result-secret"},
        status=Status.PLANNED,
        mission_id="mission-1",
        depends_on=["missing-prerequisite"],
    )

    context = director.strategic_context()

    assert context["schema_version"] == "kdo.director.strategic-context.v1"
    assert context["scope"] == {"type": "platform", "workspace_id": None}
    assert context["authority"] == "none"
    assert context["health_summary"]["unknown_observer_count"] == 1
    assert context["evidence"]["facts"]
    assert context["evidence"]["unknowns"]
    assert context["evidence"]["inferences"]
    assert context["evidence"]["engine_signals"]["accepted"][0]["capability"] == "optimize_content_schedule"
    assert "params" not in context["evidence"]["engine_signals"]["accepted"][0]

    assert context["objectives"]["model"] == "legacy_sector_goal"
    assert context["objectives"]["items"]
    assert context["objectives"]["success_criteria_defined"] is False

    capabilities = {item["name"]: item for item in context["capabilities"]["items"]}
    assert "publish_scheduled_content" not in capabilities
    assert context["capabilities"]["director_can_publish"] is False
    assert capabilities["review_content_quality"]["sector"] == "content"

    assert context["missions"]["mission_count"] == 1
    assert context["missions"]["dependency_edge_count"] == 1
    assert context["missions"]["unresolved_dependency_count"] == 1
    assert context["missions"]["recent_tasks"][0]["task_id"] == "task-1"
    assert "params" not in context["missions"]["recent_tasks"][0]
    assert "result" not in context["missions"]["recent_tasks"][0]

    assert context["learning"]["capabilities"] == {}
    assert context["guardrails"]["context_grants_execution_authority"] is False


def test_strategic_context_never_exposes_task_params_or_engine_signal_params():
    director = Director(supabase_client=None)
    director.observations = {
        "content-engine": Observation(
            source="content-engine",
            sector=Sector.CONTENT,
            status=Health.NOMINAL,
            timestamp=datetime.now(timezone.utc),
            metrics={
                "director_signal_contract": "metrics.director_signals/v1",
                "director_signals": [{
                    "capability": "optimize_content_schedule",
                    "description": "Review a schedule",
                    "params": {"api_key": "signal-secret"},
                    "confidence": 0.8,
                }],
            },
        )
    }
    director.tasks["task-secret"] = Task(
        id="task-1",
        sector=Sector.CONTENT,
        description="Safe task description",
        capability="review_content_quality",
        authority=Authority.NOTIFY,
        risk=Risk.LOW,
        params={"access_token": "task-secret"},
        result={"credential": "result-secret"},
    )

    context = director.strategic_context()
    serialized = repr(context)

    for secret in ("signal-secret", "task-secret", "result-secret"):
        assert secret not in serialized


def test_strategic_context_excludes_unverified_learning_outcomes():
    director = Director(supabase_client=None)
    signal = director.learning_loop.record_outcome(
        Task(
            id="unverified",
            sector=Sector.INFRA,
            description="A handler-reported completion",
            capability="run_health_scan",
            authority=Authority.SUGGEST,
            risk=Risk.LOW,
            status=Status.COMPLETED,
        ),
        duration_ms=100,
    )

    context = director.strategic_context()

    assert signal is None
    assert "run_health_scan" not in context["learning"]["capabilities"]


def test_strategic_context_validates_bounds():
    director = Director(supabase_client=None)

    with pytest.raises(ValueError, match="max_age_seconds"):
        director.strategic_context(max_age_seconds=0)
    with pytest.raises(ValueError, match="max_tasks"):
        director.strategic_context(max_tasks=0)


@pytest.mark.asyncio
async def test_plan_exposes_strategic_context_and_keeps_legacy_evidence_context(monkeypatch):
    director = Director(supabase_client=None)
    monkeypatch.setattr(director, "decide", lambda: [])

    plan = await director._build_plan_async()

    assert plan["strategic_context"]["schema_version"] == "kdo.director.strategic-context.v1"
    assert plan["strategic_context"]["authority"] == "none"
    assert plan["decision_context"] == plan["strategic_context"]["evidence"]
