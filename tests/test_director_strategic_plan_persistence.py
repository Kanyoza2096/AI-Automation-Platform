from types import SimpleNamespace

from fastapi.testclient import TestClient

from services.director import Director, create_app


def _objective():
    return {
        "objective_id": "obj-quality",
        "title": "Improve measured content quality",
        "description": "Diagnose the measured quality gap and recommend a bounded improvement.",
        "success_criteria": ["A verified quality measure improves."],
        "constraints": ["Do not publish content.", "Do not deploy production."],
    }


def _planner(**kwargs):
    return {
        "summary": "Review evidence before recommending schedule changes.",
        "steps": [
            {
                "step_id": "review",
                "capability": "review_content_quality",
                "sector": "content",
                "objective": "Review the evidence behind the quality gap.",
                "target": "Current quality observations and source reports",
                "verification_method": "Compare current observer reports with the stated quality criteria.",
                "depends_on": [],
                "acceptance_criteria": ["The review cites current evidence."],
                "deliverables": ["Evidence-linked assessment"],
                "rationale": "Diagnosis precedes action.",
                "criterion_refs": ["criterion:0"],
                "evidence_refs": [],
            },
            {
                "step_id": "schedule",
                "capability": "optimize_content_schedule",
                "sector": "content",
                "objective": "Recommend a bounded schedule improvement.",
                "target": "Brand scheduler configuration and verified quality assessment",
                "verification_method": "Trace the recommendation to the review without changing scheduler state.",
                "depends_on": ["review"],
                "acceptance_criteria": ["The recommendation is supported by the review."],
                "deliverables": ["Schedule recommendation"],
                "rationale": "Schedule changes remain separately governed.",
                "criterion_refs": ["criterion:0"],
                "evidence_refs": [],
            },
        ],
    }


def test_in_memory_store_round_trips_bounded_strategic_plans():
    import asyncio
    from services.director.stores import InMemoryStore

    async def run():
        store = InMemoryStore()
        plan = {"plan_id": "dplan-1", "objective": {"objective_id": "obj-1"}}
        assert await store.save_strategic_plan(plan) is True
        assert await store.get_strategic_plan("dplan-1") == plan
        assert await store.list_strategic_plans() == [plan]

    asyncio.run(run())


def test_strategic_plan_api_creates_and_reads_advisory_plan(monkeypatch):
    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    director = Director(supabase_client=None, strategic_planner=_planner)
    client = TestClient(create_app(director))
    headers = {"Authorization": "Bearer master-test-token"}

    created = client.post("/strategic-plans", headers=headers, json=_objective())
    assert created.status_code == 200
    plan = created.json()
    assert plan["status"] == "proposed"
    assert plan["authority"] == "none"
    assert plan["execution_started"] is False
    assert plan["tasks_created"] is False

    fetched = client.get(f"/strategic-plans/{plan['plan_id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["plan_id"] == plan["plan_id"]

    listed = client.get("/strategic-plans", headers=headers)
    assert listed.status_code == 200
    assert [item["plan_id"] for item in listed.json()["plans"]] == [plan["plan_id"]]


def test_strategic_plan_api_rejects_tenant_scope(monkeypatch):
    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    director = Director(supabase_client=None, strategic_planner=_planner)
    app = create_app(director)

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-tenant", role="owner", species_privileges=False
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    client = TestClient(app)
    headers = {"Authorization": "Bearer valid-user-session"}
    assert client.post("/strategic-plans", headers=headers, json=_objective()).status_code == 403
    assert client.get("/strategic-plans", headers=headers).status_code == 403


def test_strategic_plan_api_fails_closed_when_planner_is_not_configured(monkeypatch):
    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    client = TestClient(create_app(Director(supabase_client=None)))
    response = client.post(
        "/strategic-plans",
        headers={"Authorization": "Bearer master-test-token"},
        json=_objective(),
    )
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]
