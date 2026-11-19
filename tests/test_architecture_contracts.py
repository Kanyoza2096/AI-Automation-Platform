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
    assert "get_bus_sync()" in registry
    assert "set_bus_instance(bus)" in registry
    assert "bus = container.try_resolve(UnifiedEventBus)" in lifecycle
    assert "await bus.start()" in lifecycle
    assert "container.register_instance(UnifiedEventBus, bus)" in lifecycle


def test_unified_event_bus_has_one_instance_across_di_and_legacy_access() -> None:
    registry = (ROOT / "dependency_registry.py").read_text(encoding="utf-8")
    lifecycle = (ROOT / "lifecycle.py").read_text(encoding="utf-8")
    assert "Reuse the module-level instance" in registry
    assert "separate event-bus subscriber registries" in lifecycle
    assert "bus = await get_unified_bus()" in lifecycle


def test_preboot_unified_bus_adopts_bootstrapped_supabase_client() -> None:
    registry = (ROOT / "dependency_registry.py").read_text(encoding="utf-8")
    assert "supabase_client = _resolve_supabase_safely(c)" in registry
    assert "elif bus.supabase is None and supabase_client is not None:" in registry
    assert "bus.supabase = supabase_client" in registry


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
    workflow = (ROOT.parent / ".github" / "workflows" / "docker-build.yml").read_text(encoding="utf-8")
    assert "file: ./backend/Dockerfile" in workflow
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



def test_director_mission_admission_journals_collaboration_origin():
    sql = (ROOT / "migrations" / "188_director_mission_admission_provenance.sql").read_text(encoding="utf-8")
    assert "source_collaboration_run_id TEXT" in sql
    assert "director_mission_collaboration_provenance_conflict" in sql
    assert "source_collaboration_run_id', source_collaboration_run_id" in sql
    assert "'mission.admitted'" in sql
    assert "source_collaboration_provenance_sha256" in sql
    assert "director_mission_collaboration_digest_conflict" in sql
    # Mission admission remains a journal event, not an execution/approval grant.
    assert "execution_permitted" not in sql
    assert "task.approved" not in sql


def test_strategic_plan_requires_server_verified_collaboration_provenance():
    api = (ROOT / "services" / "director" / "api.py").read_text(encoding="utf-8")
    planning = (ROOT / "services" / "director" / "planning.py").read_text(encoding="utf-8")
    assert '.eq("workspace_id", workspace_id)' in api
    assert 'source_run.get("status") != "completed"' in api
    assert 'recommendation.get("status") != "available"' in api
    assert 'hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()' in api
    assert 'Source collaboration record changed after strategic-plan creation' in api
    assert 'provenance.get("sha256") != current_digest' in api
    assert 'source_collaboration_provenance_required' in planning
    assert '"source_collaboration_provenance": dict(source_provenance)' in planning


def test_verified_collaboration_digest_survives_plan_to_mission_admission():
    core = (ROOT / "services" / "director" / "core.py").read_text(encoding="utf-8")
    planning = (ROOT / "services" / "director" / "planning.py").read_text(encoding="utf-8")
    assert 'source_provenance.get("workspace_id") != workspace_id' in core
    assert '"source_collaboration_provenance_sha256": source_provenance_sha256' in core
    assert '"collaboration_provenance_sha256:" + source_provenance_sha256' in core
    assert '"source_collaboration_provenance": dict(source_provenance)' in planning


def test_finalized_collaboration_runs_are_database_immutable():
    sql = (ROOT / "migrations" / "189_director_collaboration_run_finalization_guard.sql").read_text(encoding="utf-8")
    assert "BEFORE UPDATE OR DELETE ON public.director_collaboration_runs" in sql
    assert "OLD.status <> 'running'" in sql
    assert "director_collaboration_run_finalized_immutable" in sql
    assert "director_collaboration_run_delete_forbidden" in sql
    assert "director_collaboration_run_completed_synthesis_unavailable" in sql
    assert "cardinality(NEW.requested_engines)" in sql
    assert "NULLIF(btrim(NEW.synthesis), '') IS NULL" in sql
    assert "director_collaboration_run_identity_immutable" in sql
