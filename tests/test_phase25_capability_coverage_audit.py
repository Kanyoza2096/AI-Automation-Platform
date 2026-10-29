from types import SimpleNamespace

from fastapi.testclient import TestClient

from services.director import Director, create_app


def test_capability_coverage_summary_exposes_actionable_gaps_without_claiming_runtime_health(monkeypatch):
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
    summary = body["summary"]
    catalog = body["capabilities"]

    assert summary["total"] == len(catalog)
    assert summary["missing_production_handler"] == sum(
        "production_handler_missing" in item["issues"] for item in catalog.values()
    )
    assert summary["missing_governed_handoff"] == sum(
        "governed_handoff_missing" in item["issues"] for item in catalog.values()
    )
    assert summary["runtime_dependencies_unverified"] == summary["total"]
    assert summary["runtime_dependencies_verified"] == 0
    assert summary["end_to_end_runtime_verified"] is False
    assert summary["readiness_scope"] == "static_wiring_and_contracts_only"

    assert sum(row["total"] for row in summary["by_sector"].values()) == summary["total"]
    assert sum(row["ready"] for row in summary["by_sector"].values()) == summary["ready"]
    assert summary["issue_counts"]["production_handler_missing"] == summary["missing_production_handler"]


def test_capability_issue_counts_do_not_mutate_capability_definitions():
    director = Director(supabase_client=None)
    before = {name: cap.enabled for name, cap in director.capabilities.items()}
    catalog = director.capability_catalog()

    issue_counts = {}
    for item in catalog.values():
        for issue in item["issues"]:
            issue_counts[issue] = issue_counts.get(issue, 0) + 1

    assert issue_counts.get("safety_blocked_handler", 0) > 0
    assert before == {name: cap.enabled for name, cap in director.capabilities.items()}
