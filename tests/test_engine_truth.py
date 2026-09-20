import asyncio
import inspect

from core.engine_topology import CANONICAL_ENGINE_DEPENDENCIES
from core.service_definitions import _resolve_autonomous_content, _resolve_autonomous_loop
from services.meta_engine import MetaEngineV7


def test_canonical_topology_prevents_topic_selector_content_cycle():
    assert CANONICAL_ENGINE_DEPENDENCIES["topic_selector"] == ("supabase",)
    assert "topic_selector" in CANONICAL_ENGINE_DEPENDENCIES["content_engine"]
    assert "content_engine" not in CANONICAL_ENGINE_DEPENDENCIES["topic_selector"]


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
    meta.register("topic_selector", Dependent())
    snapshot = meta._scan_all_engines(force=True)
    topic = snapshot.engines["topic_selector"]
    assert topic.failure_class == "degraded_due_to_dependency"
    assert topic.root_dependency == "supabase"
    asyncio.run(meta.stop())


def test_engine_registry_does_not_alias_job_stats_as_job_service():
    from services.director import ENGINE_REGISTRY
    matches = [row for row in ENGINE_REGISTRY if row[0] == "services.job_stats_service"]
    assert matches
    assert matches[0][2] == "job_stats_service"


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
