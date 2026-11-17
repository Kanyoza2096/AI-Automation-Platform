import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from core.platform_state import (
    CapabilityState,
    PlatformStateSnapshot,
    Readiness,
    ServiceState,
)
from services.director import Director, create_app


NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self, _client):
        pass

    async def load(self, *, scope_type="platform", scope_id="platform"):
        assert scope_type == "platform"
        assert scope_id == "platform"
        return PlatformStateSnapshot(
            revision=4,
            updated_at=NOW,
            services=(
                ServiceState(
                    service_id="director",
                    owner="director",
                    readiness=Readiness.HEALTHY,
                    observed_at=NOW,
                    source_version="health-v1",
                    dependencies=(),
                    capabilities=("strategic_planning",),
                    critical=True,
                    evidence=(),
                ),
            ),
            capabilities=(
                CapabilityState(
                    capability_id="strategic_planning",
                    owner_service="director",
                    readiness=Readiness.HEALTHY,
                    observed_at=NOW,
                ),
            ),
        )


def test_platform_state_route_returns_authoritative_snapshot_for_platform_token(monkeypatch):
    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    monkeypatch.setattr(
        "services.supabase.get_service_client_from_settings",
        lambda: object(),
    )
    monkeypatch.setattr("core.platform_state_reader.PlatformStateStore", FakeStore)
    client = TestClient(create_app(Director(supabase_client=None)))

    response = client.get(
        "/platform-state",
        headers={"Authorization": "Bearer master-test-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "available"
    assert payload["revision"] == 4
    assert payload["services"][0]["service_id"] == "director"
    assert payload["services"][0]["readiness"] == "healthy"
    assert payload["capabilities"][0]["capability_id"] == "strategic_planning"


def test_platform_state_route_denies_tenant_even_when_tenant_owner(monkeypatch):
    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    app = create_app(Director(supabase_client=None))

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="workspace-a",
            role="owner",
            species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    response = TestClient(app).get(
        "/platform-state",
        headers={"Authorization": "Bearer master-test-token"},
    )
    assert response.status_code == 403


def test_platform_state_route_requires_authentication(monkeypatch):
    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    client = TestClient(create_app(Director(supabase_client=None)))

    response = client.get("/platform-state")

    assert response.status_code == 401



def test_platform_state_route_denies_unscoped_admin_without_species_privilege(monkeypatch):
    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    app = create_app(Director(supabase_client=None))

    @app.middleware("http")
    async def non_platform_admin_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id=None,
            role="admin",
            species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    response = TestClient(app).get(
        "/platform-state",
        headers={"Authorization": "Bearer valid-user-session"},
    )
    assert response.status_code == 403


class FakePlatformStateReader:
    def __init__(self, snapshot=None, error=None):
        self.snapshot = snapshot
        self.error = error
        self.calls = 0

    async def get_snapshot(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.snapshot


def _platform_snapshot(updated_at=None, observer_observed_at=None):
    updated_at = updated_at or datetime.now(timezone.utc)
    return SimpleNamespace(
        revision=9,
        schema_version=1,
        updated_at=updated_at,
        services=(
            SimpleNamespace(
                service_id="director",
                owner="director",
                readiness=Readiness.HEALTHY,
                observed_at=updated_at,
                reason=None,
                critical=True,
                evidence=(),
            ),
            SimpleNamespace(
                service_id="job_service",
                owner="job_service",
                readiness=Readiness.DEGRADED,
                observed_at=updated_at,
                reason="dependency_degraded",
                critical=True,
                evidence=(),
            ),
        ),
        observer_observations=(
            SimpleNamespace(
                service_id="director",
                observer_id="meta_engine",
                readiness=Readiness.DEGRADED,
                observed_at=observer_observed_at or updated_at,
                source_version="meta-v7.1",
                evidence=(),
            ),
        ),
        capabilities=(
            SimpleNamespace(
                capability_id="strategic_planning",
                owner_service="director",
                readiness=Readiness.HEALTHY,
                observed_at=updated_at,
                reason=None,
            ),
        ),
    )


def test_director_refreshes_shared_platform_state_into_bounded_planning_context():
    reader = FakePlatformStateReader(_platform_snapshot())
    director = Director(supabase_client=None, platform_state_reader=reader)

    state = asyncio.run(director.refresh_platform_state_context())
    context = director.strategic_context()

    assert reader.calls == 1
    assert state["status"] == "available"
    assert state["freshness"] == "fresh"
    assert state["revision"] == 9
    assert state["readiness_counts"] == {"healthy": 1, "degraded": 1}
    assert state["services"][1]["service_id"] == "job_service"
    assert state["observer_observations"][0]["observer_id"] == "meta_engine"
    assert state["observer_observations"][0]["reported_readiness"] == "degraded"
    assert context["platform_state"]["observer_observations"][0]["readiness"] == "degraded"
    assert context["platform_state"]["revision"] == 9
    assert context["platform_state"]["policy"]["read_only"] is True
    assert context["platform_state"]["policy"]["grants_execution_authority"] is False
    assert context["authority"] == "none"


def test_director_marks_expired_platform_snapshot_stale_not_healthy():
    reader = FakePlatformStateReader(
        _platform_snapshot(datetime.now(timezone.utc) - timedelta(minutes=10))
    )
    director = Director(supabase_client=None, platform_state_reader=reader)

    state = asyncio.run(director.refresh_platform_state_context(max_age_seconds=60))

    assert state["status"] == "stale"
    assert state["freshness"] == "stale"
    assert state["reason"] == "platform_state_snapshot_expired"
    assert state["revision"] == 9
    assert state["services"][0]["readiness"] == "stale"
    assert state["services"][0]["reported_readiness"] == "healthy"
    assert state["capabilities"][0]["readiness"] == "stale"


def test_director_fails_closed_when_platform_state_reader_raises():
    reader = FakePlatformStateReader(error=RuntimeError("database unavailable"))
    director = Director(supabase_client=None, platform_state_reader=reader)

    state = asyncio.run(director.refresh_platform_state_context())

    assert state["status"] == "unavailable"
    assert state["freshness"] == "unknown"
    assert state["reason"] == "platform_state_read_failed"
    assert state["services"] == []


def test_director_reports_uninitialized_shared_state_when_snapshot_missing():
    reader = FakePlatformStateReader(snapshot=None)
    director = Director(supabase_client=None, platform_state_reader=reader)

    state = asyncio.run(director.refresh_platform_state_context())

    assert state["status"] == "uninitialized"
    assert state["freshness"] == "unknown"
    assert state["reason"] == "platform_state_snapshot_missing"


def test_director_keeps_platform_state_unknown_when_reader_is_not_configured():
    director = Director(supabase_client=None)

    state = asyncio.run(director.refresh_platform_state_context())

    assert state["status"] == "unavailable"
    assert state["freshness"] == "unknown"
    assert state["reason"] == "platform_state_reader_not_configured"


def test_explicit_strategic_plan_refreshes_platform_state_before_planner_input(monkeypatch):
    reader = FakePlatformStateReader(_platform_snapshot())
    director = Director(supabase_client=None, platform_state_reader=reader)
    captured = []

    def fake_build_strategic_plan(*, objective, context, generator):
        captured.append(context["platform_state"])
        return {"plan_id": "plan-platform-state-test", "status": "proposed"}

    async def save_plan(_plan):
        return True

    monkeypatch.setattr(
        "services.director.core.build_strategic_plan",
        fake_build_strategic_plan,
    )
    director.store.save_strategic_plan = save_plan

    result = asyncio.run(director.create_strategic_plan(
        {"objective_id": "test"},
        planner=lambda **_kwargs: {},
    ))

    assert result["status"] == "proposed"
    assert reader.calls == 1
    assert captured[0]["revision"] == 9
    assert captured[0]["freshness"] == "fresh"


def test_director_downgrades_old_meta_observation_even_when_snapshot_is_fresh():
    now = datetime.now(timezone.utc)
    reader = FakePlatformStateReader(
        _platform_snapshot(now, observer_observed_at=now - timedelta(minutes=10))
    )
    director = Director(supabase_client=None, platform_state_reader=reader)

    state = asyncio.run(director.refresh_platform_state_context(max_age_seconds=60))

    assert state["freshness"] == "fresh"
    assert state["observer_observations"][0]["freshness"] == "stale"
    assert state["observer_observations"][0]["readiness"] == "stale"
