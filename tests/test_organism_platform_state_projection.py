import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from core.platform_state import Readiness
from api.organism_body_routes import _platform_state_for_species


def _request(is_species):
    return SimpleNamespace(
        state=SimpleNamespace(
            auth_identity=SimpleNamespace(is_species=is_species)
        )
    )


def _snapshot(updated_at=None):
    updated_at = updated_at or datetime.now(timezone.utc)
    return SimpleNamespace(
        revision=21,
        schema_version=1,
        updated_at=updated_at,
        services=(
            SimpleNamespace(
                service_id="director",
                owner="director",
                readiness=Readiness.HEALTHY,
                reason=None,
            ),
        ),
        observer_observations=(
            SimpleNamespace(
                service_id="director",
                observer_id="meta_engine",
                readiness=Readiness.DEGRADED,
                observed_at=updated_at,
                source_version="meta-v7.1",
            ),
        ),
        capabilities=(
            SimpleNamespace(
                capability_id="strategic_planning",
                owner_service="director",
                readiness=Readiness.HEALTHY,
            ),
        ),
    )


class FakeStore:
    snapshot = None

    def __init__(self, _client):
        pass

    async def load(self, *, scope_type="platform", scope_id="platform"):
        assert scope_type == "platform"
        assert scope_id == "platform"
        return self.snapshot


def test_tenant_vitals_cannot_read_platform_wide_state(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "services.supabase.get_service_client_from_settings",
        lambda: calls.append("service_client") or object(),
    )

    result = asyncio.run(_platform_state_for_species(_request(False)))

    assert result is None
    assert calls == []


def test_species_vitals_can_read_platform_state_snapshot(monkeypatch):
    FakeStore.snapshot = _snapshot()
    monkeypatch.setattr(
        "services.supabase.get_service_client_from_settings",
        lambda: object(),
    )
    monkeypatch.setattr("core.platform_state_reader.PlatformStateStore", FakeStore)

    result = asyncio.run(_platform_state_for_species(_request(True)))

    assert result["status"] == "available"
    assert result["freshness"] == "fresh"
    assert result["revision"] == 21
    assert result["services"][0]["service_id"] == "director"
    assert result["capabilities"][0]["capability_id"] == "strategic_planning"
    assert result["observer_observations"][0]["observer_id"] == "meta_engine"
    assert result["observer_observations"][0]["readiness"] == "degraded"
    assert result["policy"]["platform_operator_only"] is True
    assert result["policy"]["grants_execution_authority"] is False


def test_species_vitals_never_displays_expired_readiness_as_healthy(monkeypatch):
    FakeStore.snapshot = _snapshot(datetime.now(timezone.utc) - timedelta(minutes=10))
    monkeypatch.setattr(
        "services.supabase.get_service_client_from_settings",
        lambda: object(),
    )
    monkeypatch.setattr("core.platform_state_reader.PlatformStateStore", FakeStore)

    result = asyncio.run(_platform_state_for_species(_request(True)))

    assert result["status"] == "stale"
    assert result["services"][0]["readiness"] == "stale"
    assert result["services"][0]["reported_readiness"] == "healthy"


def test_species_vitals_fails_closed_when_shared_state_read_fails(monkeypatch):
    class BrokenStore:
        def __init__(self, _client):
            pass

        async def load(self, **_kwargs):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        "services.supabase.get_service_client_from_settings",
        lambda: object(),
    )
    monkeypatch.setattr("core.platform_state_reader.PlatformStateStore", BrokenStore)

    result = asyncio.run(_platform_state_for_species(_request(True)))

    assert result["status"] == "unavailable"
    assert result["freshness"] == "unknown"
    assert result["services"] == []
