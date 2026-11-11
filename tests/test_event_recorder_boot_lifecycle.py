from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import lifecycle


@pytest.mark.parametrize("mode", ["full", "api", "core", ""])
def test_event_recorder_is_owned_by_web_business_modes(monkeypatch, mode):
    monkeypatch.setenv("SERVICE_MODE", mode)

    steps = lifecycle._build_boot_sequence(SimpleNamespace(), SimpleNamespace())

    assert "EventRecorder" in {step.name for step in steps}


@pytest.mark.parametrize("mode", ["workers", "director", "meta", "condition", "monitor", "github"])
def test_event_recorder_is_not_started_in_non_business_process_modes(monkeypatch, mode):
    monkeypatch.setenv("SERVICE_MODE", mode)

    steps = lifecycle._build_boot_sequence(SimpleNamespace(), SimpleNamespace())

    assert "EventRecorder" not in {step.name for step in steps}


def test_event_recorder_boot_registers_async_shutdown(monkeypatch):
    calls = []

    class FakeRecorder:
        started = False
        stopped = False

        async def start(self):
            self.started = True

        async def stop(self):
            self.stopped = True

    recorder = FakeRecorder()

    class FakeContainer:
        def resolve(self, service_type):
            return recorder

    async def register(name, hook, timeout_seconds):
        calls.append((name, hook, timeout_seconds))

    monkeypatch.setattr(lifecycle.shutdown_registry, "register", register)

    asyncio.run(lifecycle._boot_event_recorder(FakeContainer()))

    assert recorder.started
    assert len(calls) == 1
    assert calls[0][0] == "EventRecorder"
    assert calls[0][1] == recorder.stop
    assert calls[0][2] == 5.0


def test_failed_event_recorder_start_does_not_register_shutdown(monkeypatch):
    calls = []

    class FakeRecorder:
        async def start(self):
            raise RuntimeError("subscription failed")

    class FakeContainer:
        def resolve(self, service_type):
            return FakeRecorder()

    async def register(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(lifecycle.shutdown_registry, "register", register)

    with pytest.raises(RuntimeError, match="subscription failed"):
        asyncio.run(lifecycle._boot_event_recorder(FakeContainer()))

    assert calls == []
