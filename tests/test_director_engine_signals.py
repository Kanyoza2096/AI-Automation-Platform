from datetime import datetime, timedelta, timezone

from services.director import Director
from services.director.models import Health, Observation, Sector, Status


def _observation(*, sector=Sector.CONTENT, age_seconds=0, signals=None):
    return Observation(
        source="test-engine",
        sector=sector,
        status=Health.NOMINAL,
        metrics={"director_signals": signals or []},
        timestamp=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    )


def test_fresh_registered_engine_signal_is_included_in_decision_context():
    director = Director(supabase_client=None)
    director.observations["content-engine"] = _observation(signals=[{
        "capability": "optimize_content_schedule",
        "description": "Review content schedule gap",
        "params": {"brand_id": "brand-7"},
        "confidence": 0.82,
        "rationale": "The engine detected an uncovered publishing window.",
    }])

    context = director.decision_context()
    signals = context["engine_signals"]["accepted"]

    assert len(signals) == 1
    assert signals[0]["capability"] == "optimize_content_schedule"
    assert signals[0]["observer"] == "content-engine"


def test_stale_engine_signal_is_rejected():
    director = Director(supabase_client=None)
    director.observations["content-engine"] = _observation(age_seconds=600, signals=[{
        "capability": "optimize_content_schedule",
        "description": "Stale recommendation",
        "params": {},
    }])

    result = director._engine_decision_signals()
    assert result["accepted"] == []
    assert result["rejected"][0]["reason"] == "observation_not_fresh"


def test_engine_signal_cannot_select_capability_from_another_sector():
    director = Director(supabase_client=None)
    director.observations["content-engine"] = _observation(signals=[{
        "capability": "prepare_security_remediation",
        "description": "Attempt cross-sector action",
        "params": {"issue_id": "issue-1"},
    }])

    result = director._engine_decision_signals()
    assert result["accepted"] == []
    assert result["rejected"][0]["reason"] == "sector_mismatch"


def test_valid_engine_signal_creates_advisory_task_without_bypassing_policy():
    director = Director(supabase_client=None)
    director.observations["content-engine"] = _observation(signals=[{
        "capability": "optimize_content_schedule",
        "description": "Review schedule gap from engine",
        "params": {"brand_id": "brand-7"},
        "confidence": 0.8,
    }])

    tasks = director.decide()
    matches = [task for task in tasks if task.description == "Review schedule gap from engine"]

    assert len(matches) == 1
    assert matches[0].capability == "optimize_content_schedule"
    assert matches[0].status in (Status.PLANNED, Status.APPROVAL)
    assert "engine_signal:content-engine" in matches[0].sources


def test_engine_signal_success_criteria_and_ranking_fields_flow_to_task():
    director = Director(supabase_client=None)
    director.observations["content-engine"] = _observation(signals=[{
        "capability": "optimize_content_schedule",
        "description": "Close high-impact schedule gap",
        "params": {"brand_id": "brand-7"},
        "confidence": 0.9,
        "impact_score": 95,
        "mission_priority": 80,
        "success_criteria": ["A schedule gap is no longer present"],
    }])

    tasks = director.decide()
    task = next(task for task in tasks if task.description == "Close high-impact schedule gap")

    assert task.impact_score == 95
    assert task.mission_priority == 80
    assert task.success_criteria == ["A schedule gap is no longer present"]
    graph_node = next(node for node in director.mission_graph()["nodes"] if node["task_id"] == task.id)
    assert graph_node["impact_score"] == 95
    assert graph_node["mission_priority"] == 80


def test_engine_signal_with_invalid_ranking_or_success_criteria_is_rejected():
    director = Director(supabase_client=None)
    director.observations["content-engine"] = _observation(signals=[
        {"capability": "optimize_content_schedule", "description": "Bad impact", "impact_score": 101},
        {"capability": "optimize_content_schedule", "description": "Bad criteria", "success_criteria": "not-a-list"},
    ])

    result = director._engine_decision_signals()
    assert result["accepted"] == []
    assert [item["reason"] for item in result["rejected"]] == [
        "impact_score_out_of_range", "invalid_success_criteria"
    ]


def test_priority_score_considers_impact_and_mission_priority():
    director = Director(supabase_client=None)
    base = director._create_task(
        Sector.CONTENT, "Base", "optimize_content_schedule", "test", 0.8, {},
        impact_score=10, mission_priority=10,
    )
    high_value = director._create_task(
        Sector.CONTENT, "High value", "optimize_content_schedule", "test", 0.8, {"brand_id": "different"},
        impact_score=90, mission_priority=90,
    )
    assert director._priority(high_value) > director._priority(base)


def test_engine_signal_accepts_structured_observer_success_criteria():
    director = Director(supabase_client=None)
    criterion = {
        "observer": "authoritative_domain_observer",
        "metric_path": "brands.brand-7.active",
        "operator": "equals",
        "expected": True,
    }
    director.observations["content-engine"] = _observation(signals=[{
        "capability": "optimize_content_schedule",
        "description": "Verify structured schedule outcome",
        "success_criteria": [criterion],
    }])

    result = director._engine_decision_signals()

    assert len(result["accepted"]) == 1
    assert result["accepted"][0]["success_criteria"] == [criterion]


def test_engine_signal_from_unhealthy_observer_is_rejected():
    director = Director(supabase_client=None)
    director.observations["content-engine"] = Observation(
        source="test-engine",
        sector=Sector.CONTENT,
        status=Health.DEGRADED,
        metrics={"director_signals": [{
            "capability": "optimize_content_schedule",
            "description": "Recommendation from degraded observer",
            "params": {},
        }]},
        timestamp=datetime.now(timezone.utc),
    )

    result = director._engine_decision_signals()

    assert result["accepted"] == []
    assert result["rejected"][0]["reason"] == "observer_status_not_healthy"


def test_engine_signal_is_rejected_when_fresh_sector_observers_disagree():
    director = Director(supabase_client=None)
    director.observations["content-engine"] = _observation(signals=[{
        "capability": "optimize_content_schedule",
        "description": "Recommendation amid conflicting health evidence",
        "params": {},
    }])
    director.observations["content-monitor"] = Observation(
        source="content-monitor",
        sector=Sector.CONTENT,
        status=Health.DEGRADED,
        metrics={},
        timestamp=datetime.now(timezone.utc),
    )

    result = director._engine_decision_signals()

    assert result["accepted"] == []
    assert result["rejected"][0]["reason"] == "sector_status_conflict"


def test_stale_issue_observation_does_not_create_remediation_task():
    director = Director(supabase_client=None)
    director.observations["old-service-monitor"] = Observation(
        source="old-service-monitor",
        sector=Sector.HEALTH,
        status=Health.CRITICAL,
        issues=[{"type": "failed_service", "service_name": "legacy-worker"}],
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=20),
    )

    tasks = director.decide()

    assert not any(
        task.capability == "restart_failed_service"
        and task.sources == ["old-service-monitor"]
        for task in tasks
    )


def test_signal_coverage_exposes_observers_that_do_not_publish_contract():
    director = Director(supabase_client=None)
    director.observations["content-engine"] = Observation(
        source="content-engine",
        sector=Sector.CONTENT,
        status=Health.NOMINAL,
        metrics={"queue_depth": 4},
        timestamp=datetime.now(timezone.utc),
    )

    result = director._engine_decision_signals()

    assert result["coverage_summary"]["observer_count"] == 1
    assert result["coverage_summary"]["contract_enabled_observers"] == 0
    assert result["coverage_summary"]["observers_without_signal_contract"] == ["content-engine"]
    assert result["coverage"][0]["has_signal_contract"] is False


def test_engine_signal_approval_cannot_bypass_proposal_governance():
    import asyncio

    director = Director(supabase_client=None)
    calls = []
    director.observations["content-engine"] = _observation(signals=[{
        "capability": "optimize_content_schedule",
        "description": "Review schedule gap from engine",
        "params": {"brand_id": "brand-7"},
        "confidence": 0.9,
    }])
    capability = director.capabilities["optimize_content_schedule"]
    capability.handler = lambda params: calls.append(params) or {"ok": True}

    tasks = director.decide()
    task = next(task for task in tasks if task.description == "Review schedule gap from engine")
    assert "engine_signal:content-engine" in task.sources

    # Simulate a generic task approval. It must not authorize a legacy handler.
    task.status = Status.APPROVED
    result = asyncio.run(director._execute_async(task.id))

    assert result.status == Status.APPROVAL
    assert result.error == "engine_signal_requires_proposal_governance"
    assert calls == []


def test_signal_coverage_requires_versioned_contract_marker():
    director = Director(supabase_client=None)
    director.observations["legacy-content-engine"] = _observation(signals=[{
        "capability": "optimize_content_schedule",
        "description": "Legacy unversioned recommendation",
        "params": {},
    }])

    result = director._engine_decision_signals()
    assert result["coverage_summary"]["contract_enabled_observers"] == 0
    assert result["coverage"][0]["has_signal_contract"] is False
    # Keep backward compatibility for legacy publishers while distinguishing
    # them from publishers that explicitly implement the versioned contract.
    assert len(result["accepted"]) == 1


def test_signal_with_unsupported_explicit_contract_is_rejected():
    director = Director(supabase_client=None)
    obs = _observation(signals=[{
        "capability": "optimize_content_schedule",
        "description": "Unsupported contract signal",
        "params": {},
    }])
    obs.metrics["director_signal_contract"] = "metrics.director_signals/v99"
    director.observations["content-engine"] = obs

    result = director._engine_decision_signals()
    assert result["accepted"] == []
    assert any(item["reason"] == "unsupported_signal_contract" for item in result["rejected"])


def test_signal_batch_over_limit_is_rejected_instead_of_silently_truncated():
    director = Director(supabase_client=None)
    director.observations["content-engine"] = _observation(signals=[{}] * 51)

    result = director._engine_decision_signals()
    assert result["accepted"] == []
    assert result["rejected"][-1]["reason"] == "signal_count_limit_exceeded"


def test_enabled_goals_do_not_create_tasks_without_evidence():
    director = Director(supabase_client=None)

    tasks = director.decide()

    assert not any(task.description.startswith("Goal-driven:") for task in tasks)
    assert not any(task.capability in {
        "run_guardian_scan",
        "calculate_brand_health",
        "review_content_quality",
        "analyze_growth_opportunity",
        "generate_client_report",
    } for task in tasks)
