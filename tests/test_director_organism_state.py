from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from services.director import Director, create_app
from services.director.models import Health, Observation, Sector


def _observation(name, status=Health.NOMINAL, timestamp=None, metrics=None, issues=None):
    return Observation(
        source=name,
        sector=Sector.INFRA,
        status=status,
        metrics=metrics or {},
        issues=issues or [],
        message="source observation",
        timestamp=timestamp or datetime.now(timezone.utc),
    )


def test_organism_state_preserves_source_provenance_and_freshness():
    director = Director(supabase_client=None)
    director.observations = {
        "api": _observation("api-monitor", metrics={"latency_ms": 42}),
        "queue": _observation(
            "queue-monitor",
            status=Health.DEGRADED,
            timestamp=datetime.now(timezone.utc) - timedelta(minutes=20),
            issues=[{"type": "backlog"}],
        ),
    }

    state = director.organism_state(max_age_seconds=300)

    assert state["condition"] == "degraded_evidence"
    assert state["summary"]["observation_count"] == 2
    assert state["summary"]["fresh_count"] == 1
    assert state["summary"]["stale_count"] == 1
    api = next(item for item in state["observations"] if item["observer"] == "api")
    queue = next(item for item in state["observations"] if item["observer"] == "queue")
    assert api["source"] == "api-monitor"
    assert api["metrics"] == {"latency_ms": 42}
    assert api["freshness"] == "fresh"
    assert queue["freshness"] == "stale"
    assert queue["issues"] == [{"type": "backlog"}]
    assert state["evidence_policy"]["missing_values_synthesized"] is False


def test_organism_state_reports_unknown_when_no_observations_exist():
    state = Director(supabase_client=None).organism_state()
    assert state["condition"] == "unknown"
    assert state["summary"]["observation_count"] == 0
    assert state["observations"] == []


def test_organism_state_does_not_call_stale_critical_evidence_current_health():
    director = Director(supabase_client=None)
    director.observations = {
        "old-critical": _observation(
            "old-critical", status=Health.CRITICAL,
            timestamp=datetime.now(timezone.utc) - timedelta(hours=1),
        ),
    }
    state = director.organism_state()
    assert state["condition"] == "stale"
    assert state["summary"]["stale_count"] == 1


def test_organism_state_api_requires_auth_and_validates_freshness_window(monkeypatch):
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

    client = TestClient(app)
    response = client.get("/organism/state", headers={"Authorization": "Bearer valid-user-session"})
    assert response.status_code == 200
    assert response.json()["condition"] == "unknown"

    bad = client.get(
        "/organism/state?max_age_seconds=0",
        headers={"Authorization": "Bearer valid-user-session"},
    )
    assert bad.status_code == 400


def test_decision_context_separates_facts_inferences_unknowns_and_conflicts():
    director = Director(supabase_client=None)
    director.observations = {
        "api-a": _observation("api-a", status=Health.NOMINAL),
        "api-b": _observation("api-b", status=Health.DEGRADED, issues=[{"type": "latency"}]),
        "old-queue": _observation(
            "old-queue", status=Health.CRITICAL,
            timestamp=datetime.now(timezone.utc) - timedelta(hours=1),
        ),
        "unknown": _observation("unknown", status=Health.UNKNOWN),
    }

    context = director.decision_context(max_age_seconds=300)

    assert context["version"] == 1
    assert {fact["observer"] for fact in context["facts"]} == {"api-a", "api-b"}
    assert any(item["kind"] == "reported_issue_requires_triage" for item in context["inferences"])
    assert {item["kind"] for item in context["unknowns"]} >= {
        "observation_not_current", "source_status_unknown",
    }
    assert context["conflicts"] == [{
        "kind": "sector_status_disagreement",
        "sector": "infra",
        "statuses": ["degraded", "nominal"],
        "observers": ["api-a", "api-b"],
        "interpretation": "Fresh observers disagree; do not collapse this into a single authoritative status.",
    }]
    assert context["evidence_policy"]["inferences_are_not_verified_outcomes"] is True


def test_decision_context_marks_absence_as_unknown_not_healthy():
    context = Director(supabase_client=None).decision_context()
    assert context["facts"] == []
    assert context["unknowns"][0]["kind"] == "no_observations"
    assert context["organism_condition"] == "unknown"


def test_decision_context_api_exposes_structured_evidence_and_validates_window(monkeypatch):
    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    monkeypatch.setenv("PYTHON_SERVICE_TOKEN", "service-test-token")
    director = Director(supabase_client=None)
    director.observations = {"api": _observation("api")}
    app = create_app(director)

    @app.middleware("http")
    async def canonical_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-test", role="owner", species_privileges=True,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    client = TestClient(app)
    response = client.get("/decision-context", headers={"Authorization": "Bearer valid-user-session"})
    assert response.status_code == 200
    assert response.json()["facts"][0]["observer"] == "api"

    bad = client.get(
        "/decision-context?max_age_seconds=86401",
        headers={"Authorization": "Bearer valid-user-session"},
    )
    assert bad.status_code == 400
