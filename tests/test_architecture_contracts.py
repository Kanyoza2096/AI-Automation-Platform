"""Architecture-level regression tests for release identity and event authority."""

from __future__ import annotations

from pathlib import Path

from platform_version import PLATFORM_NAME, PLATFORM_VERSION

ROOT = Path(__file__).resolve().parents[1]


def test_platform_release_identity_is_canonical() -> None:
    assert PLATFORM_NAME == "Kanyoza Systems AI Platform"
    assert PLATFORM_VERSION == "14.7.5"
    source = (ROOT / "platform_version.py").read_text(encoding="utf-8")
    assert f'PLATFORM_VERSION = "{PLATFORM_VERSION}"' in source


def test_runtime_consumes_canonical_release_identity() -> None:
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    settings = (ROOT / "config" / "settings.py").read_text(encoding="utf-8")
    assert "from platform_version import PLATFORM_VERSION" in main
    assert "from platform_version import PLATFORM_VERSION" in settings
    assert '"version": PLATFORM_VERSION' in main


def test_unified_event_bus_is_composition_root_authority() -> None:
    registry = (ROOT / "dependency_registry.py").read_text(encoding="utf-8")
    lifecycle = (ROOT / "lifecycle.py").read_text(encoding="utf-8")
    assert "c.register(UnifiedEventBus" in registry
    assert "container.register_instance(UnifiedEventBus, bus)" in lifecycle


def test_legacy_event_bus_is_explicitly_bridged() -> None:
    legacy = (ROOT / "core" / "event_bus.py").read_text(encoding="utf-8")
    registry = (ROOT / "dependency_registry.py").read_text(encoding="utf-8")
    assert "LEGACY" in legacy
    assert "EventBusBridge" in legacy
    assert "get_event_bus_bridge" in registry


def test_director_proxy_uses_lifespan_owned_http_client() -> None:
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'app.state.director_http_client = httpx.AsyncClient(' in main
    assert 'await shutdown_registry.register(\n            "DirectorHTTPClient"' in main
    assert 'client = getattr(request.app.state, "director_http_client", None)' in main
    assert 'async with httpx.AsyncClient(' not in main


def test_docker_ci_builds_canonical_root_dockerfile() -> None:
    candidates = [ROOT / ".github" / "workflows" / "docker-build.yml", ROOT.parent / ".github" / "workflows" / "docker-build.yml"]
    workflow_path = next((candidate for candidate in candidates if candidate.exists()), None)
    assert workflow_path is not None, "canonical docker-build workflow is missing"
    workflow = workflow_path.read_text(encoding="utf-8")
    assert "file: ./Dockerfile" in workflow
    assert "docker/${{ matrix.service }}.Dockerfile" not in workflow


def test_durable_authority_migration_normalizes_execution_tenant_type_and_rpc_signature():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sql = (root / "migrations" / "033_durable_authority_hardening.sql").read_text(encoding="utf-8")
    assert "ALTER COLUMN workspace_id TYPE TEXT USING workspace_id::text" in sql
    assert "begin_external_execution(TEXT, TEXT, TEXT, TEXT, JSONB)" in sql
    assert "begin_external_execution(UUID, TEXT, TEXT, TEXT, JSONB)" in sql
    assert "user_id = auth.uid()::text" in sql
    assert "claim_publish_outbox(INTEGER, INTEGER)" in sql
    assert "p_lease_seconds < 30 OR p_lease_seconds > 3600" in sql
