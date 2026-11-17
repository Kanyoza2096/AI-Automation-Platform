from services.director.observation_bridge import project_meta_health_evidence


def _health():
    return {
        "available": True,
        "status": "degraded",
        "health_score": 81,
        "risk_score": 22,
        "engine_observations": [
            {
                "service_id": "guardian",
                "status": "healthy",
                "observed_at": "2026-11-17T03:00:00+00:00",
                "age_seconds": 12,
                "source_version": "guardian-v2",
            },
            {
                "service_id": "scheduler",
                "status": "degraded",
                "observed_at": "2026-11-17T03:00:00+00:00",
                "age_seconds": 15,
                "source_version": "scheduler-v1",
            },
        ],
        "engine_observation_contract": {
            "contract": "meta_engine.observations/v1",
            "status": "complete",
            "source_count": 2,
            "accepted_count": 2,
            "rejected_count": 0,
            "freshness_seconds": 3600,
            "generated_at": "2026-11-17T03:00:12+00:00",
            "rejected": [],
        },
    }


def test_projection_preserves_supervisor_status_and_limits_authority():
    result = project_meta_health_evidence(_health())

    assert result["status"] == "complete"
    assert result["reported_system_status"] == "degraded"
    assert [item["service_id"] for item in result["observations"]] == ["guardian", "scheduler"]
    assert result["observations"][1]["status"] == "degraded"
    assert result["observations"][0]["independently_verified"] is False
    assert result["policy"]["authority"] == "supervisor_reported"
    assert result["policy"]["grants_execution_authority"] is False


def test_projection_does_not_invent_observations_when_contract_is_missing():
    health = {"available": True, "status": "healthy", "engine_states": {"guardian": "healthy"}}

    result = project_meta_health_evidence(health)

    assert result["status"] == "unavailable"
    assert result["observations"] == []
    assert result["policy"]["unknown_is_not_healthy"] is True


def test_projection_flags_contract_count_mismatch():
    health = _health()
    health["engine_observation_contract"]["accepted_count"] = 9

    result = project_meta_health_evidence(health)

    assert result["status"] == "inconsistent"
    assert result["integrity_status"] == "accepted_count_mismatch"


def test_projection_filters_malformed_observation_rows():
    health = _health()
    health["engine_observations"].append({"service_id": "missing-timestamp", "status": "healthy"})
    health["engine_observation_contract"]["accepted_count"] = 3

    result = project_meta_health_evidence(health)

    assert len(result["observations"]) == 2
    assert result["integrity_status"] == "accepted_count_mismatch"


def test_director_decision_context_includes_read_only_meta_supervisor_evidence(monkeypatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from services.director import Director, create_app
    import services.director.api as director_api

    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    monkeypatch.setenv("PYTHON_SERVICE_TOKEN", "service-test-token")
    expected = project_meta_health_evidence(_health())
    monkeypatch.setattr(director_api, "read_meta_health_evidence", lambda: expected)

    app = create_app(Director(supabase_client=None))

    @app.middleware("http")
    async def canonical_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-test", role="owner", species_privileges=True,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    response = TestClient(app).get(
        "/decision-context",
        headers={"Authorization": "Bearer valid-user-session"},
    )

    assert response.status_code == 200
    payload = response.json()["supervisor_observation_evidence"]
    assert payload["source"] == "meta_engine.check_health"
    assert payload["policy"]["grants_execution_authority"] is False
    assert payload["observations"][1]["status"] == "degraded"
