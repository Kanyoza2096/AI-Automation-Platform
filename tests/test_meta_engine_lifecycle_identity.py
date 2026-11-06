import asyncio


def test_meta_engine_boot_publishes_canonical_singleton_to_container(monkeypatch):
    import lifecycle
    import services.meta_engine as meta_package
    from services.meta_engine import MetaEngineV7

    class FakeMetaEngine:
        async def stop(self):
            return None

    class FakeContainer:
        def __init__(self):
            self.instances = {}

        def try_resolve(self, _service_type):
            return None

        def register_instance(self, service_type, instance):
            self.instances[service_type] = instance

    meta = FakeMetaEngine()
    container = FakeContainer()

    async def noop_register(*args, **kwargs):
        return None

    monkeypatch.setattr(meta_package, "meta_engine", meta)
    monkeypatch.setattr(meta_package, "init_meta_engine", lambda **_kwargs: meta)
    monkeypatch.setattr(lifecycle.shutdown_registry, "register", noop_register)
    monkeypatch.setattr(lifecycle.health_registry, "register", noop_register)

    asyncio.run(lifecycle._boot_meta_engine(container))

    assert container.instances[MetaEngineV7] is meta


def test_meta_health_handlers_attach_to_canonical_singleton(monkeypatch):
    import lifecycle
    import services.meta_engine as meta_package
    import services.engine_health_handlers as handlers

    canonical_meta = object()
    received = []

    monkeypatch.setattr(meta_package, "meta_engine", canonical_meta)
    monkeypatch.setattr(
        handlers,
        "register_all_health_handlers",
        lambda meta: received.append(meta) or {"registered": ["test"]},
    )

    asyncio.run(lifecycle._boot_meta_health_handlers(container=None))

    assert received == [canonical_meta]
