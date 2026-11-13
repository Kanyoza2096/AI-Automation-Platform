import asyncio
import inspect

from core.engine_topology import CANONICAL_ENGINE_DEPENDENCIES
from core.service_definitions import _resolve_autonomous_content, _resolve_autonomous_loop
from services.meta_engine import MetaEngineV7


def test_brand_scoped_topic_selector_is_not_a_platform_service():
    assert "topic_selector" not in CANONICAL_ENGINE_DEPENDENCIES
    assert "topic_selector" not in CANONICAL_ENGINE_DEPENDENCIES["content_engine"]
    from core.service_definitions import SERVICE_DEFINITIONS
    assert "topic_selector" not in {name for name, _resolver, _critical in SERVICE_DEFINITIONS}
    from services.engine_health_handlers import HEALTH_HANDLERS
    assert "topic_selector" not in HEALTH_HANDLERS
    from services.director import ENGINE_REGISTRY
    assert not any(row[2] == "topic_selector" for row in ENGINE_REGISTRY)


def test_meta_graph_matches_canonical_topology():
    meta = MetaEngineV7()
    for name, deps in CANONICAL_ENGINE_DEPENDENCIES.items():
        assert meta._deps.get_dependencies(name) == list(deps)
    asyncio.run(meta.stop())


def test_autonomous_resolvers_return_instances_not_coroutines(monkeypatch):
    class Container:
        def try_resolve(self, _type):
            return None

    loop_value = _resolve_autonomous_loop(Container())
    content_value = _resolve_autonomous_content(Container())
    assert not inspect.isawaitable(loop_value)
    assert not inspect.isawaitable(content_value)


def test_meta_probe_supports_async_health_contracts():
    class AsyncEngine:
        async def check_health_async(self):
            await asyncio.sleep(0)
            return {"status": "healthy"}

    meta = MetaEngineV7()
    meta.register("director", AsyncEngine())
    assert meta._ping_engine(AsyncEngine(), "director") is True
    asyncio.run(meta.stop())


def test_failure_classification_marks_dependency_cascade():
    meta = MetaEngineV7()

    class Engine:
        def health_check(self):
            return {"status": "offline"}

    class Dependent:
        def health_check(self):
            return {"status": "offline"}

    meta.register("supabase", Engine())
    meta.register("brand_scheduler", Dependent())
    snapshot = meta._scan_all_engines(force=True)
    scheduler = snapshot.engines["brand_scheduler"]
    assert scheduler.failure_class == "degraded_due_to_dependency"
    assert scheduler.root_dependency == "supabase"
    asyncio.run(meta.stop())


def test_engine_registry_does_not_alias_job_stats_as_job_service():
    from services.director import ENGINE_REGISTRY
    matches = [row for row in ENGINE_REGISTRY if row[0] == "services.job_stats_service"]
    assert matches
    assert matches[0][2] == "job_stats_service"


def test_canonical_scheduler_identity_is_brand_scheduler():
    assert "brand_scheduler" in CANONICAL_ENGINE_DEPENDENCIES
    assert "scheduler" not in CANONICAL_ENGINE_DEPENDENCIES
    assert CANONICAL_ENGINE_DEPENDENCIES["brand_scheduler"] == (
        "job_service", "content_engine", "supabase"
    )
    from core.service_definitions import SERVICE_DEFINITIONS
    service_names = {name for name, _resolver, _critical in SERVICE_DEFINITIONS}
    assert "brand_scheduler" in service_names
    assert "scheduler" not in service_names
    from services.director import ENGINE_REGISTRY
    scheduler_rows = [row for row in ENGINE_REGISTRY if row[1] == "BrandSchedulerManager"]
    assert scheduler_rows and scheduler_rows[0][2] == "brand_scheduler"
    from services.director.analysis import Sector, SystemTopology
    topology = SystemTopology()
    assert topology.nodes["brand_scheduler"].sector is Sector.CONTENT
    assert topology.nodes["brand_scheduler"].criticality == 3
    from services.engine_health_handlers import NATIVE_HEALTH_ENGINES
    assert "brand_scheduler" in NATIVE_HEALTH_ENGINES
    assert "scheduler" not in NATIVE_HEALTH_ENGINES
    assert "brand_scheduler" in MetaEngineV7.EXTERNAL_SERVICES


def test_meta_engine_has_distinct_job_service_and_job_stats_nodes():
    meta = MetaEngineV7()
    assert meta._deps.get_dependencies("job_service") == ["supabase", "event_bus"]
    assert meta._deps.get_dependencies("job_stats_service") == ["supabase"]
    asyncio.run(meta.stop())


def test_canonical_topology_is_acyclic_and_dependencies_are_declared():
    from core.engine_topology import validate_topology
    drift = validate_topology(CANONICAL_ENGINE_DEPENDENCIES.keys())
    assert drift.healthy is True
    assert drift.invalid_dependencies == ()
    assert drift.cycles == ()


def test_topology_validator_detects_registration_drift():
    from core.engine_topology import validate_topology
    drift = validate_topology({"supabase", "event_bus", "rogue_engine"})
    assert "rogue_engine" in drift.undeclared_runtime_nodes
    assert "content_engine" in drift.missing_runtime_nodes


def test_meta_rejects_awaitable_engine_registration():
    class AsyncEngine:
        async def build(self):
            return object()

    async def factory():
        return AsyncEngine()

    meta = MetaEngineV7()
    try:
        meta.register("bad_async_engine", factory)
        raise AssertionError("expected awaitable registration to be rejected")
    except TypeError as exc:
        assert "awaitable" in str(exc)
    finally:
        asyncio.run(meta.stop())



def test_reachable_engine_without_health_evidence_is_unknown_not_nominal():
    class ReachableButUnverified:
        def health_check(self):
            return {"available": True}

    meta = MetaEngineV7()
    try:
        meta.register("unverified_engine", ReachableButUnverified())
        health = meta.check_engine_health("unverified_engine", force=True)
        assert health.status.value == "unknown"
        assert health.health_score < 100
        assert health.risk_score > 0
    finally:
        asyncio.run(meta.stop())


def test_async_health_contract_is_used_for_reported_status():
    class AsyncHealthyEngine:
        async def check_health_async(self):
            return {"status": "healthy", "available": True}

    meta = MetaEngineV7()
    try:
        engine = AsyncHealthyEngine()
        meta.register("async_healthy_engine", engine)
        assert meta._reported_engine_status(engine, "async_healthy_engine") == "healthy"
        health = meta.check_engine_health("async_healthy_engine", force=True)
        assert health.status.value == "nominal"
    finally:
        asyncio.run(meta.stop())



def test_system_snapshot_is_unknown_when_engine_health_is_unverified():
    class ReachableButUnverified:
        def health_check(self):
            return {"available": True}

    meta = MetaEngineV7()
    try:
        meta.register("unverified_engine", ReachableButUnverified())
        snapshot = meta._scan_all_engines(force=True)
        assert snapshot.engines["unverified_engine"].status.value == "unknown"
        assert snapshot.overall_status.value == "unknown"
        assert meta.check_health()["status"] == "unknown"
    finally:
        asyncio.run(meta.stop())


def test_meta_registration_rejects_unresolved_callable_without_invoking_it():
    called = []
    def request_handler():
        called.append(True)
        return object()

    meta = MetaEngineV7()
    try:
        try:
            meta.register("unsafe_handler", request_handler)
            raise AssertionError("expected callable export to be rejected")
        except TypeError as exc:
            assert "explicit lifecycle adapter" in str(exc)
        assert called == []
        assert "unsafe_handler" not in meta._engines
    finally:
        asyncio.run(meta.stop())


def test_meta_engine_discovery_reports_skipped_callable_exports(monkeypatch):
    import types
    import services.director as director_pkg
    import services.meta_engine.core as meta_core

    module = types.ModuleType("fake_discovery_module")
    def request_handler():
        raise AssertionError("discovery must not invoke this")
    module.request_handler = request_handler
    # Meta Engine imports the registry from services.director at runtime.
    monkeypatch.setattr(director_pkg, "ENGINE_REGISTRY", [
        ("fake_discovery_module", "request_handler", "unsafe_handler", "infra")
    ])
    import sys
    monkeypatch.setitem(sys.modules, "fake_discovery_module", module)
    meta = MetaEngineV7()
    try:
        meta.register_all_engines()
        assert "unsafe_handler" not in meta._engines
        assert any(item["engine"] == "unsafe_handler" for item in meta._registration_report["skipped"])
    finally:
        asyncio.run(meta.stop())


def test_service_registry_rejects_factories_and_awaitables():
    from core.service_registry import ServiceRegistry

    registry = ServiceRegistry()
    calls = []

    def factory():
        calls.append("called")
        return object()

    try:
        registry.register("factory", factory)
        raise AssertionError("expected factory export to be rejected")
    except TypeError as exc:
        assert "live instance" in str(exc)
    assert calls == []

    async def async_factory():
        return object()

    pending = async_factory()
    try:
        registry.register("pending", pending)
        raise AssertionError("expected awaitable to be rejected")
    except TypeError as exc:
        assert "awaitable" in str(exc)
    assert pending.cr_frame is None


def test_service_registry_topology_report_is_explicitly_not_health():
    from core.service_registry import ServiceRegistry

    registry = ServiceRegistry()
    service = object()
    registry.register("event_bus", service, auto_register=False)
    report = registry.get_topology_report()

    assert report["source"] == "service_registry_instances"
    assert report["runtime_nodes"] == ["event_bus"]
    assert report["health_evaluated"] is False
    assert "job_service" in report["missing_runtime_nodes"]
    assert report["topology_complete"] is False


def test_service_registry_registration_report_returns_defensive_copy():
    from core.service_registry import ServiceRegistry

    registry = ServiceRegistry()
    registry._last_registration_report = {
        "registered": ["event_bus"],
        "skipped": [{"service": "task_queue", "reason": "resolved_none"}],
        "failed": [],
        "meta_registration_failures": [],
    }
    report = registry.get_registration_report()
    report["registered"].append("rogue")
    report["skipped"][0]["reason"] = "edited"

    fresh = registry.get_registration_report()
    assert fresh["registered"] == ["event_bus"]
    assert fresh["skipped"][0]["reason"] == "resolved_none"


def test_service_registry_reports_meta_instance_identity_drift():
    from core.service_registry import ServiceRegistry

    class Meta:
        def __init__(self):
            self._engines = {"event_bus": object()}

    registry = ServiceRegistry()
    registry.set_meta_engine(Meta())
    registry.register("event_bus", object(), auto_register=False)
    report = registry.get_topology_report()

    assert report["identity_mismatches"] == ["event_bus"]
    assert report["missing_from_meta_engine"] == []


def test_service_definition_registration_report_tracks_missing_optional_service(monkeypatch):
    import core.service_definitions as definitions
    from core.service_registry import ServiceRegistry

    registry = ServiceRegistry()
    monkeypatch.setattr("core.service_registry.get_service_registry", lambda: registry)
    monkeypatch.setattr(definitions, "SERVICE_DEFINITIONS", [
        ("present_service", lambda _container: object(), False),
        ("optional_missing", lambda _container: None, False),
        ("critical_missing", lambda _container: None, True),
    ])

    count = definitions.register_all_services(object(), None)
    report = registry.get_registration_report()

    assert count == 1
    assert report["registered"] == ["present_service"]
    assert report["skipped"] == [{"service": "optional_missing", "reason": "resolved_none", "critical": False}]
    assert report["failed"] == [{"service": "critical_missing", "reason": "resolved_none", "critical": True}]


def test_meta_engine_normalizes_director_observer_alias_to_canonical_name(monkeypatch):
    from types import SimpleNamespace
    import services.director as director_package

    live_director = SimpleNamespace(service_name="director-instance")
    monkeypatch.setattr(director_package, "_p4_test_director_instance", live_director, raising=False)
    monkeypatch.setattr(director_package, "ENGINE_REGISTRY", [
        ("services.director", "_p4_test_director_instance", "meta_director", None),
    ])

    meta = MetaEngineV7()
    try:
        meta.register_all_engines()
        assert meta._engines.get("director") is live_director
        assert "meta_director" not in meta._engines
        assert meta._registration_report["normalized_aliases"] == [
            {"source_name": "meta_director", "canonical_name": "director"}
        ]
    finally:
        asyncio.run(meta.stop())


def test_service_container_instance_lookup_does_not_trigger_lazy_factory():
    from service_container import ServiceContainer

    class Service:
        pass

    calls = []
    container = ServiceContainer()
    container.register(Service, lambda: calls.append("constructed") or Service())

    assert container.try_get_instance(Service) is None
    assert calls == []
    instance = container.resolve(Service)
    assert container.try_get_instance(Service) is instance
    assert calls == ["constructed"]


def test_lifecycle_service_resolvers_do_not_lazily_construct_optional_services():
    from core.service_definitions import _resolve_ai_orchestrator, _resolve_plugin_registry
    from service_container import ServiceContainer

    class AIService:
        pass

    class PluginRegistryService:
        pass

    container = ServiceContainer()
    ai_calls = []
    plugin_calls = []
    container.register(AIService, lambda: ai_calls.append(True) or AIService())
    container.register(PluginRegistryService, lambda: plugin_calls.append(True) or PluginRegistryService())

    # These service definitions resolve their real production types. A registered
    # but not-yet-created service must not be created as a side effect of monitoring.
    assert _resolve_ai_orchestrator(container) is None
    assert _resolve_plugin_registry(container) is None
    assert ai_calls == []
    assert plugin_calls == []


def test_service_resolvers_do_not_start_async_or_worker_services_during_collection(monkeypatch):
    import core.service_definitions as definitions
    import core.render_queue as render_queue
    import services.job_stats_service as job_stats

    class EmptyContainer:
        def try_get_instance(self, _service_type):
            return None

    monkeypatch.setattr(job_stats, "job_stats_service", None)
    monkeypatch.setattr(render_queue, "_render_queue", None)

    assert definitions._resolve_event_bus(EmptyContainer()) is None
    assert definitions._resolve_job_stats_service(EmptyContainer()) is None
    assert definitions._resolve_render_queue(EmptyContainer()) is None
    assert render_queue._render_queue is None
    assert job_stats.job_stats_service is None


def test_director_observer_resolves_async_health_contract_without_fabricating_status():
    import asyncio
    from services.director.core import _make_observer

    class AsyncHealthyEngine:
        async def check_health_async(self):
            await asyncio.sleep(0)
            return {"status": "healthy", "queue_depth": 2}

    observer = _make_observer("async_engine", AsyncHealthyEngine())
    result = asyncio.run(asyncio.to_thread(observer))
    assert result["status"] == "healthy"
    assert result["metrics"]["queue_depth"] == 2
    assert result["health_contract"] == "check_health_async"


def test_director_observer_keeps_async_payload_without_status_unknown():
    import asyncio
    from services.director.core import _make_observer

    class AsyncUnverifiedEngine:
        async def check_health_async(self):
            return {"available": True}

    observer = _make_observer("unverified_async_engine", AsyncUnverifiedEngine())
    result = asyncio.run(asyncio.to_thread(observer))
    assert result["status"] == "unknown"
    assert result["health_contract"] == "check_health_async"


def test_director_observer_exception_is_unknown_not_engine_critical():
    from services.director.analysis import FunctionObserver
    from services.director.models import Health, Sector

    def broken_probe():
        raise RuntimeError("probe transport failed")

    observation = FunctionObserver("broken_probe", Sector.INFRA, broken_probe).observe()
    assert observation.status == Health.UNKNOWN
    assert observation.issues[0]["type"] == "observer_failure"
    assert observation.issues[0]["evidence_confidence"] == "low"


def test_director_discovery_reuses_existing_container_instance_for_class_export(monkeypatch):
    import sys
    import types
    import services.director.core as director_core
    from services.director.core import Director, auto_discover_engines
    from services.director.models import Sector

    class Engine:
        def health_check(self):
            return {"status": "healthy", "queue_depth": 1}

    live_engine = Engine()
    module = types.ModuleType("p9_live_instance_module")
    module.Engine = Engine
    monkeypatch.setitem(sys.modules, "p9_live_instance_module", module)
    monkeypatch.setattr(director_core, "ENGINE_REGISTRY", [
        ("p9_live_instance_module", "Engine", "container_engine", Sector.INFRA),
    ])

    class Container:
        def __init__(self):
            self.lookups = []
        def try_get_instance(self, service_type):
            self.lookups.append(service_type)
            return live_engine if service_type is Engine else None
        def try_resolve(self, _service_type):
            raise AssertionError("Director discovery must not use lazy resolution")

    container = Container()
    director = Director()
    try:
        count = auto_discover_engines(director, container)
        assert count == 1
        assert "container_engine" in director.observers
        assert container.lookups == [Engine]
        observation = director.observers["container_engine"].observe()
        assert observation.status.value == "nominal"
    finally:
        # The Director constructor owns resources in some configurations.
        stop = getattr(director, "shutdown", None)
        if callable(stop):
            result = stop()
            if asyncio.iscoroutine(result):
                asyncio.run(result)


def test_supervisor_evidence_keeps_meta_and_director_sources_separate():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from routes.backend_contract_routes import _supervisor_evidence

    checked_at = datetime.now(timezone.utc)
    meta = SimpleNamespace(_current_snapshot=SimpleNamespace(
        overall_status=SimpleNamespace(value="degraded"),
        timestamp=checked_at,
        health_score=62.0,
        risk_score=38.0,
        engines={"job_service": SimpleNamespace(
            status=SimpleNamespace(value="degraded"),
            checked_at=checked_at,
            health_score=50.0,
            risk_score=50.0,
            failure_class="dependency_failure",
            root_dependency="supabase",
            dependencies=["supabase", "event_bus"],
            anomaly_flags=["probe_error"],
        )},
    ))
    director = SimpleNamespace(organism_state=lambda max_age_seconds: {
        "condition": "degraded_evidence",
        "summary": {"observation_count": 1, "unknown_status_count": 1},
        "observations": [{"observer": "job_service", "reported_status": "unknown"}],
    })

    evidence = _supervisor_evidence(meta, director)
    assert evidence["meta_engine"]["status"] == "degraded"
    assert evidence["meta_engine"]["engines"]["job_service"]["root_dependency"] == "supabase"
    assert evidence["director"]["condition"] == "degraded_evidence"
    assert evidence["director"]["observations"][0]["reported_status"] == "unknown"
    assert "no health signals are merged" in evidence["policy"]


def test_director_system_topology_matches_all_canonical_nodes_and_edges():
    from core.engine_topology import CANONICAL_ENGINE_DEPENDENCIES
    from services.director.analysis import SystemTopology

    topology = SystemTopology()
    assert set(topology.nodes) == set(CANONICAL_ENGINE_DEPENDENCIES)
    for name, dependencies in CANONICAL_ENGINE_DEPENDENCIES.items():
        node = topology.nodes[name]
        assert node.depends_on == set(dependencies), name
        for dependency in dependencies:
            assert name in topology.nodes[dependency].depended_by


def test_director_topology_preserves_metadata_while_projecting_canonical_nodes():
    from services.director.analysis import Sector, SystemTopology

    topology = SystemTopology()

    assert topology.get_service_sector("director") == Sector.SELF
    assert topology.get_service_criticality("director") == 5
    assert topology.get_service_sector("gemini") == Sector.HEALTH
    assert topology.get_service_criticality("gemini") == 3
    assert topology.get_service_sector("github") == Sector.SECURITY
    assert topology.get_service_criticality("github") == 3


def test_engine_runtime_map_covers_canonical_topology_and_is_explicitly_static():
    from core.engine_topology import CANONICAL_ENGINE_DEPENDENCIES
    from scripts.audit_engine_topology import build_engine_inventory, render_markdown

    rows = build_engine_inventory()
    assert {row["engine"] for row in rows} == set(CANONICAL_ENGINE_DEPENDENCIES)
    assert len(rows) == len(CANONICAL_ENGINE_DEPENDENCIES)
    director_row = next(row for row in rows if row["engine"] == "director")
    assert "services.director.director" in director_row["director_discovery"]
    rendered = render_markdown(rows)
    assert "not a live health report" in rendered
    assert "`autonomous_loop`" in rendered


def test_topology_registration_presence_does_not_infer_health():
    from routes.backend_contract_routes import _topology_status

    assert _topology_status("registered-but-unprobed", {}) == "unknown"
    assert _topology_status("registered-but-error", {"registered-but-error": "error"}) == "error"
    assert _topology_status("live", {"live": "nominal"}) == "healthy"
    assert _topology_status("stopped", {"stopped": "stopped"}) == "degraded"


def test_registered_health_adapter_is_used_for_status_not_only_liveness():
    class AdapterOnlyEngine:
        pass

    meta = MetaEngineV7()
    try:
        engine = AdapterOnlyEngine()
        meta.register("adapter_only", engine)
        meta.register_health_handler(
            "adapter_only",
            lambda _engine: {"status": "healthy", "evidence": "adapter"},
        )
        assert meta._reported_engine_status(engine, "adapter_only") == "healthy"
        health = meta.check_engine_health("adapter_only", force=True)
        assert health.status.value == "nominal"
    finally:
        asyncio.run(meta.stop())


def test_idle_is_a_valid_non_degraded_engine_status():
    class IdleEngine:
        def health_check(self):
            return {"status": "idle", "active_jobs": 0}

    meta = MetaEngineV7()
    try:
        engine = IdleEngine()
        meta.register("idle_engine", engine)
        health = meta.check_engine_health("idle_engine", force=True)
        assert health.status.value == "nominal"
    finally:
        asyncio.run(meta.stop())


def test_typed_health_report_status_is_normalized():
    from enum import Enum
    from types import SimpleNamespace

    class Status(Enum):
        HEALTHY = "healthy"

    class TypedReportEngine:
        async def check_health_async(self):
            return SimpleNamespace(status=Status.HEALTHY)

    meta = MetaEngineV7()
    try:
        engine = TypedReportEngine()
        meta.register("typed_report_engine", engine)
        assert meta._reported_engine_status(engine, "typed_report_engine") == "healthy"
        assert meta.check_engine_health("typed_report_engine", force=True).status.value == "nominal"
    finally:
        asyncio.run(meta.stop())


def test_task_queue_health_adapter_distinguishes_running_idle_and_full():
    from services.engine_health_handlers import task_queue_health

    class Queue:
        def __init__(self, stats):
            self._stats = stats

        def stats(self):
            return self._stats

    assert task_queue_health(Queue({
        "running": True, "queued": 0, "maxsize": 10,
    }))["status"] == "healthy"
    assert task_queue_health(Queue({
        "running": False, "queued": 0, "maxsize": 10,
    }))["status"] == "unknown"
    assert task_queue_health(Queue({
        "running": True, "queued": 10, "maxsize": 10,
    }))["status"] == "degraded"

def test_task_queue_health_adapter_is_registered_with_meta_engine():
    from services.engine_health_handlers import HEALTH_HANDLERS, task_queue_health

    assert HEALTH_HANDLERS["task_queue"] is task_queue_health


def test_render_queue_health_uses_aggregate_health_check_before_task_status():
    from services.engine_health_handlers import render_queue_health

    class RenderQueue:
        def get_status(self, task_id):
            raise AssertionError("task-specific status must not be used for service health")

        def health_check(self):
            return {"status": "healthy", "stats": {"running": True}}

    assert render_queue_health(RenderQueue())["status"] == "healthy"
