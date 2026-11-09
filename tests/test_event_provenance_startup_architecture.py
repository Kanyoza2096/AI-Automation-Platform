from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_startup_provenance_installs_on_the_di_canonical_event_bus():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    registry = (ROOT / "dependency_registry.py").read_text(encoding="utf-8")
    lifecycle = (ROOT / "lifecycle.py").read_text(encoding="utf-8")

    assert "provenance_bus = container.try_resolve(UnifiedEventBus)" in main
    assert "install_tenant_facebook_provenance(" in main
    assert "provenance_bus," in main
    assert "c.register(UnifiedEventBus, _build_unified_bus, singleton=True)" in registry
    assert "bus = container.try_resolve(UnifiedEventBus)" in lifecycle
    assert "await bus.start()" in lifecycle
    assert "UnifiedEventBus(" not in main


def test_startup_does_not_publish_producer_capability_through_app_state():
    main = (ROOT / "main.py").read_text(encoding="utf-8")

    assert "app.state.tenant_facebook_provenance_runtime" not in main
    assert "app.state.tenant_facebook_provenance_enabled = False" in main
    assert "app.state.tenant_facebook_provenance_key_id" in main
    assert "app.state.tenant_facebook_provenance_enabled = True" in main


def test_startup_provenance_composition_is_opt_in_and_does_not_block_normal_boot():
    main = (ROOT / "main.py").read_text(encoding="utf-8")

    start = main.index("install_tenant_facebook_provenance(")
    end = main.index("redis_client = _resolve_redis(container)", start)
    composition = main[start:end]

    assert "except Exception:" in composition
    assert "provenance composition failed closed" in composition
    assert "logger.info(" in composition
