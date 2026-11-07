import pytest

from config.settings import settings
from core.workspace_context import (
    clear_workspace_context,
    get_workspace_context,
    require_workspace_context,
)


def test_unbound_workspace_context_does_not_use_environment_default(monkeypatch):
    clear_workspace_context()
    monkeypatch.setattr(settings, "default_workspace_id", "production-workspace")
    with pytest.raises(PermissionError, match="No trusted WorkspaceContext"):
        get_workspace_context()


def test_explicit_legacy_default_requires_opt_in(monkeypatch):
    clear_workspace_context()
    monkeypatch.setattr(settings, "default_workspace_id", "explicit-legacy-workspace")
    context = require_workspace_context(allow_default=True)
    assert context.workspace_id == "explicit-legacy-workspace"
    clear_workspace_context()


@pytest.mark.asyncio
async def test_unified_bus_rejects_tenant_event_without_workspace():
    from core.unified_bus import UnifiedEventBus

    clear_workspace_context()
    bus = UnifiedEventBus()
    try:
        event_id = await bus.publish("tenant.observation", {"value": 1})
        assert event_id is None
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_unified_bus_keeps_platform_events_without_tenant_context():
    from core.unified_bus import UnifiedEventBus

    clear_workspace_context()
    bus = UnifiedEventBus()
    try:
        event_id = await bus.publish("heartbeat.system", {"healthy": True})
        assert event_id is not None
    finally:
        await bus.stop()


def test_application_startup_never_creates_a_tenant_from_process_default():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    lifecycle = (root / "lifecycle.py").read_text(encoding="utf-8")
    main = (root / "main.py").read_text(encoding="utf-8")
    assert "tenant organisms start after authenticated workspace binding" in lifecycle
    assert "get_or_create(default_ws" not in lifecycle
    assert "canonical_dream_system.workspace_id = None" in main

def test_strict_tenant_event_rejects_synthetic_default_workspace():
    from core.nerve import NerveContractError, validate_and_normalize

    with pytest.raises(NerveContractError, match="invalid workspace_id='default'"):
        validate_and_normalize(
            "tenant.observation",
            {"workspace_id": "default"},
            strict=True,
        )
