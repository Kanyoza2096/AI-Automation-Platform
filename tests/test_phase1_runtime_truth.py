import asyncio

import pytest

from lifecycle import BootFailure, BootPhase, BootStep, _build_boot_sequence, boot_infrastructure
from service_container import ServiceContainer
from core.shared_event_bus import SharedEventBus


def _names(mode: str) -> list[str]:
    import os
    old = os.environ.get("SERVICE_MODE")
    os.environ["SERVICE_MODE"] = mode
    try:
        return [step.name for step in _build_boot_sequence(None, ServiceContainer())]
    finally:
        if old is None:
            os.environ.pop("SERVICE_MODE", None)
        else:
            os.environ["SERVICE_MODE"] = old


def test_split_service_modes_boot_their_advertised_engine():
    assert "Director" in _names("director")
    assert "MetaEngine" in _names("meta")
    assert "ConditionEngine" in _names("condition")


def test_split_service_modes_do_not_boot_unrelated_engines():
    director = _names("director")
    meta = _names("meta")
    condition = _names("condition")

    assert "MetaEngine" not in director
    assert "ConditionEngine" not in director
    assert "Director" not in meta
    assert "ConditionEngine" not in meta
    assert "Director" not in condition
    assert "MetaEngine" not in condition


def test_shared_event_bus_is_cross_instance_only():
    assert "SharedEventBus" not in _names("api")
    assert "SharedEventBus" in _names("workers")


def test_shared_event_bus_exposes_health_registry_identity():
    assert SharedEventBus().service_name == "shared_event_bus"


@pytest.mark.asyncio
async def test_critical_boot_failure_is_not_swallowed(monkeypatch):
    async def fail(_container):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        "lifecycle._build_boot_sequence",
        lambda app, container: [
            BootStep("critical", BootPhase.INFRASTRUCTURE, fail),
        ],
    )

    with pytest.raises(BootFailure):
        await boot_infrastructure(None, ServiceContainer())
