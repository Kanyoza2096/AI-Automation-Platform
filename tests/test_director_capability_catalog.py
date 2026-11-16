import pytest
from services.director import Director
from services.director.models import Authority, Capability, Risk, Sector, Status


def test_capability_catalog_exposes_handler_and_governance_gaps():
    director = Director(supabase_client=None)
    catalog = director.capability_catalog()

    schedule = catalog["update_brand_posting_schedule"]
    assert schedule["definition_source"] == "built_in_registry"
    assert schedule["catalog_type"] == "predefined"
    assert schedule["proposal_capable"] is True
    assert schedule["handler_bound"] is False
    assert schedule["governed_handoff_bound"] is False
    assert "governed_handoff_missing" in schedule["issues"]
    assert "production_handler_missing" in schedule["issues"]
    assert schedule["ready"] is False

    normal = catalog["self_diagnostic"]
    assert normal["handler_bound"] is False
    assert "production_handler_missing" in normal["issues"]


def test_register_capability_rejects_missing_handler():
    director = Director(supabase_client=None)
    cap = Capability(
        name="invalid", sector=Sector.SELF, description="invalid",
        handler=None, authority=Authority.SUGGEST, risk=Risk.LOW,
    )
    try:
        director.register_capability(cap)
    except TypeError as exc:
        assert "callable handler" in str(exc)
    else:
        raise AssertionError("non-callable capability handler should be rejected")


def test_create_task_deduplicates_same_active_capability_and_params():
    director = Director(supabase_client=None)
    first = director._create_task(
        Sector.HEALTH, "Run health scan", "run_health_scan", "first observation",
        params={"scope": "all"}, source="engine-a",
    )
    second = director._create_task(
        Sector.HEALTH, "Run health scan again", "run_health_scan", "repeat observation",
        params={"scope": "all"}, source="engine-b",
    )

    assert second.id == first.id
    assert second.status == Status.PLANNED
    assert second.sources == ["engine-a", "engine-b"]
    assert len(director.tasks) == 1


def test_create_task_does_not_deduplicate_distinct_params_or_proposals():
    director = Director(supabase_client=None)
    first = director._create_task(
        Sector.HEALTH, "Restart A", "restart_failed_service", "issue A",
        params={"service_name": "a"}, source="engine-a",
    )
    distinct = director._create_task(
        Sector.HEALTH, "Restart B", "restart_failed_service", "issue B",
        params={"service_name": "b"}, source="engine-a",
    )
    proposal_one = director._create_task(
        Sector.CONTENT, "Proposal one", "update_brand_posting_schedule", "proposal",
        params={"brand_id": "brand-1"}, source="proposal:p1",
    )
    proposal_two = director._create_task(
        Sector.CONTENT, "Proposal two", "update_brand_posting_schedule", "proposal",
        params={"brand_id": "brand-1"}, source="proposal:p2",
    )

    assert len({first.id, distinct.id, proposal_one.id, proposal_two.id}) == 4


def test_capabilities_api_returns_readiness_summary(monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from services.director import create_app

    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    monkeypatch.setenv("PYTHON_SERVICE_TOKEN", "service-test-token")
    director = Director(supabase_client=None)
    app = create_app(director)

    @app.middleware("http")
    async def canonical_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-test", role="owner", species_privileges=True,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    response = TestClient(app).get(
        "/capabilities", headers={"Authorization": "Bearer valid-user-session"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total"] == len(director.capabilities)
    assert body["summary"]["not_ready"] > 0
    assert body["summary"]["approval_required"] > 0
    assert body["summary"]["safety_blocked"] > 0
    assert "proposal_governed_handoff" in body["summary"]["execution_paths"]
    assert body["capabilities"]["update_brand_posting_schedule"]["ready"] is False


def test_capability_catalog_exposes_execution_and_authorization_contracts():
    director = Director(supabase_client=None)
    catalog = director.capability_catalog()

    schedule = catalog["update_brand_posting_schedule"]
    assert schedule["execution_path"] == "proposal_governed_handoff"
    assert schedule["execution_boundary"] == "canonical_action_kernel_receipt_required"
    assert schedule["effective_authority"] == Authority.SUGGEST.value
    assert schedule["approval_required"] is True
    assert schedule["authorization_requirements"]["direct_execute_api"] == (
        "authenticated_write_request_and_platform_scope"
    )
    assert schedule["authorization_requirements"]["proposal_approval"] is True
    assert "governed_handoff_missing" in schedule["issues"]

    critical = catalog["drop_database"]
    assert critical["execution_path"] == "safety_blocked"
    assert critical["implementation_ready"] is False
    assert "safety_blocked_handler" in critical["issues"]
    assert critical["approval_required"] is True

    auto = catalog["run_health_scan"]
    assert auto["execution_path"] == "director_task_handler"
    assert auto["approval_required"] is False


@pytest.mark.asyncio
async def test_direct_execute_cannot_bypass_proposal_governance_or_approval():
    director = Director(supabase_client=None)
    calls = []

    director.capabilities["update_brand_posting_schedule"].handler = (
        lambda params: calls.append(params) or {"ok": True}
    )
    proposal_result = await director.execute_capability(
        "update_brand_posting_schedule", {"brand_id": "brand-1"}
    )
    assert proposal_result["status"] == "governance_required"
    assert calls == []

    director.capabilities["optimize_infrastructure"].handler = (
        lambda params: calls.append(params) or {"ok": True}
    )
    approval_result = await director.execute_capability(
        "optimize_infrastructure", {"component": "queue"}
    )
    assert approval_result["status"] == "approval_required"
    assert calls == []


@pytest.mark.asyncio
async def test_direct_execute_allows_enabled_auto_capability_with_bound_handler():
    director = Director(supabase_client=None)
    calls = []
    def run_scan(params):
        calls.append(params)
        return {"ok": True, "status": "completed"}

    director.capabilities["run_health_scan"].handler = run_scan

    result = await director.execute_capability("run_health_scan", {"scope": "all"})
    assert result["ok"] is True
    assert calls == [{"scope": "all"}]


def test_terminal_issue_task_is_not_recreated_for_same_observation():
    from datetime import datetime, timezone
    director = Director(supabase_client=None)
    observed_at = datetime.now(timezone.utc)
    first = director._create_task(
        Sector.HEALTH, "Recover failed service", "restart_failed_service",
        "Recoverable service failure", params={"service_name": "worker-a"},
        source="health-observer", evidence_observed_at=observed_at,
    )
    first.status = Status.FAILED

    repeated = director._create_task(
        Sector.HEALTH, "Recover failed service", "restart_failed_service",
        "Recoverable service failure", params={"service_name": "worker-a"},
        source="health-observer", evidence_observed_at=observed_at,
    )

    assert repeated.id == first.id
    assert len(director.tasks) == 1


def test_new_observation_can_create_a_new_task_for_recurring_condition():
    from datetime import datetime, timezone, timedelta
    director = Director(supabase_client=None)
    first_observation = datetime.now(timezone.utc)
    first = director._create_task(
        Sector.HEALTH, "Recover failed service", "restart_failed_service",
        "Recoverable service failure", params={"service_name": "worker-a"},
        source="health-observer", evidence_observed_at=first_observation,
    )
    first.status = Status.FAILED

    second = director._create_task(
        Sector.HEALTH, "Recover failed service", "restart_failed_service",
        "Recoverable service failure", params={"service_name": "worker-a"},
        source="health-observer", evidence_observed_at=first_observation + timedelta(minutes=2),
    )

    assert second.id != first.id
    assert len(director.tasks) == 2
    assert second.evidence_observed_at > first.evidence_observed_at


def test_terminal_task_without_evidence_has_short_repeat_cooldown():
    from datetime import timedelta
    director = Director(supabase_client=None)
    first = director._create_task(
        Sector.HEALTH, "Run health scan", "run_health_scan", "health score low",
        params={"scope": "all"},
    )
    first.status = Status.COMPLETED

    repeated = director._create_task(
        Sector.HEALTH, "Run health scan", "run_health_scan", "health score low",
        params={"scope": "all"},
    )
    assert repeated.id == first.id

    first.created_at = first.created_at - timedelta(minutes=16)
    next_scan = director._create_task(
        Sector.HEALTH, "Run health scan", "run_health_scan", "health score low",
        params={"scope": "all"},
    )
    assert next_scan.id != first.id


def test_terminal_task_is_not_recreated_for_same_condition_across_fresh_observations():
    from datetime import datetime, timezone, timedelta
    director = Director(supabase_client=None)
    params = {"service_name": "worker-a", "type": "failed_service"}
    fingerprint = director._condition_fingerprint("restart_failed_service", params, "health-observer")
    first = director._create_task(
        Sector.HEALTH, "Recover failed service", "restart_failed_service",
        "Recoverable service failure", params=params, source="health-observer",
        evidence_observed_at=datetime.now(timezone.utc),
        condition_fingerprint=fingerprint,
    )
    first.status = Status.FAILED

    repeated = director._create_task(
        Sector.HEALTH, "Recover failed service", "restart_failed_service",
        "Recoverable service failure", params=params, source="health-observer",
        evidence_observed_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        condition_fingerprint=fingerprint,
    )

    assert repeated.id == first.id
    assert len(director.tasks) == 1


def test_new_condition_identity_can_create_a_distinct_task_after_terminal_outcome():
    director = Director(supabase_client=None)
    params = {"service_name": "worker-a", "type": "failed_service"}
    first_fingerprint = director._condition_fingerprint("restart_failed_service", params, "health-observer")
    first = director._create_task(
        Sector.HEALTH, "Recover failed service", "restart_failed_service",
        "First observed failure", params=params, source="health-observer",
        condition_fingerprint=first_fingerprint,
    )
    first.status = Status.FAILED

    second_fingerprint = director._condition_fingerprint("restart_failed_service", params, "health-observer")
    second = director._create_task(
        Sector.HEALTH, "Recover failed service", "restart_failed_service",
        "New incident", params=params, source="health-observer",
        condition_fingerprint=second_fingerprint,
    )

    assert second.id != first.id
    assert len(director.tasks) == 2
