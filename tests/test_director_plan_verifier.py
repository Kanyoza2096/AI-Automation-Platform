from datetime import datetime, timedelta, timezone

from services.director.plan_verifier import verify_strategic_plan, _fingerprint


def _context(now=None):
    now = now or datetime.now(timezone.utc)
    return {
        "schema_version": "kdo.director.strategic-context.v1",
        "generated_at": now.isoformat(),
        "health_summary": {"status": "nominal"},
        "evidence": {"facts": [], "inferences": [], "unknowns": [], "conflicts": []},
        "objectives": {"items": []},
        "capabilities": {
            "items": [{
                "name": "run_tests",
                "sector": "engineering",
                "enabled": True,
                "handler_bound": True,
            }]
        },
        "missions": {"recent_tasks": []},
        "learning": {"capabilities": {}},
        "platform_state": {
            "capabilities": [{
                "capability_id": "run_tests",
                "readiness": "healthy",
            }]
        },
    }


def _plan(context, *, now=None):
    return {
        "schema_version": "kdo.director.strategic-plan.v1",
        "plan_id": "dplan_test",
        "objective": {
            "objective_id": "obj_test",
            "success_criteria": ["tests pass"],
        },
        "steps": [{
            "step_id": "step_1",
            "capability": "run_tests",
            "sector": "engineering",
            "depends_on": [],
            "acceptance_criteria": ["all tests pass"],
            "deliverables": ["test report"],
            "criterion_refs": ["criterion:0"],
            "evidence_refs": ["fact:0"],
        }],
        "source_context_fingerprint": _fingerprint(context),
        "authority": "none",
        "advisory_only": True,
        "execution_started": False,
        "tasks_created": False,
    }


def test_verified_plan_is_only_eligible_for_governance_review():
    now = datetime.now(timezone.utc)
    context = _context(now)
    report = verify_strategic_plan(_plan(context), context, now=now)

    assert report["verdict"] == "verified_for_governance_review"
    assert report["execution_permitted"] is False
    assert report["task_creation_permitted"] is False
    assert report["authority"] == "none"


def test_stale_context_requires_replanning():
    now = datetime.now(timezone.utc)
    context = _context(now - timedelta(minutes=20))
    report = verify_strategic_plan(_plan(context), context, now=now, max_age_seconds=60)

    assert report["verdict"] == "replan_required"
    assert "context_snapshot_stale" in report["warnings"]


def test_context_fingerprint_mismatch_requires_replanning():
    now = datetime.now(timezone.utc)
    context = _context(now)
    plan = _plan(context)
    context["health_summary"]["status"] = "degraded"

    report = verify_strategic_plan(plan, context, now=now)

    assert report["verdict"] == "replan_required"
    assert "plan_context_fingerprint_mismatch" in report["warnings"]


def test_unknown_capability_rejects_plan():
    now = datetime.now(timezone.utc)
    context = _context(now)
    plan = _plan(context)
    plan["steps"][0]["capability"] = "invented_capability"

    report = verify_strategic_plan(plan, context, now=now)

    assert report["verdict"] == "rejected"
    assert "unknown_capability:invented_capability" in report["errors"]


def test_unbound_capability_blocks_plan_without_granting_authority():
    now = datetime.now(timezone.utc)
    context = _context(now)
    context["capabilities"]["items"][0]["handler_bound"] = False
    plan = _plan(context)
    plan["source_context_fingerprint"] = _fingerprint(context)

    report = verify_strategic_plan(plan, context, now=now)

    assert report["verdict"] == "blocked"
    assert report["capabilities_not_ready"] == ["run_tests"]
    assert report["execution_permitted"] is False


def test_dependency_cycle_rejects_plan():
    now = datetime.now(timezone.utc)
    context = _context(now)
    plan = _plan(context)
    plan["steps"] = [
        {**plan["steps"][0], "step_id": "a", "depends_on": ["b"]},
        {**plan["steps"][0], "step_id": "b", "depends_on": ["a"]},
    ]

    report = verify_strategic_plan(plan, context, now=now)

    assert report["verdict"] == "rejected"
    assert "dependency_cycle" in report["errors"]


def test_missing_objective_traceability_and_evidence_require_review():
    now = datetime.now(timezone.utc)
    context = _context(now)
    plan = _plan(context)
    del plan["steps"][0]["criterion_refs"]
    del plan["steps"][0]["evidence_refs"]

    report = verify_strategic_plan(plan, context, now=now)

    assert report["verdict"] == "review_required"
    assert "objective_criterion_mapping_not_supplied" in report["warnings"]
    assert "plan_step_evidence_not_cited" in report["warnings"]


def test_future_context_timestamp_fails_closed():
    now = datetime.now(timezone.utc)
    context = _context(now + timedelta(minutes=10))
    report = verify_strategic_plan(_plan(context), context, now=now)

    assert report["verdict"] == "rejected"
    assert "context_timestamp_in_future" in report["errors"]
