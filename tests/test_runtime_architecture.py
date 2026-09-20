"""Runtime architecture contracts for event-loop safety and health probing."""

from __future__ import annotations

import asyncio
import time

import pytest

from lifecycle import HealthStatus, ServiceHealthRegistry


class _BlockingProvider:
    service_name = "blocking-provider"

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.called_on_loop = False

    def health_check(self) -> dict[str, str]:
        self.called_on_loop = True
        time.sleep(self.delay)
        return {"status": "healthy"}


class _AsyncProvider:
    service_name = "async-provider"

    async def check_health_async(self) -> dict[str, str]:
        await asyncio.sleep(0)
        return {"status": "healthy"}


@pytest.mark.asyncio
async def test_sync_health_probe_runs_off_event_loop() -> None:
    registry = ServiceHealthRegistry()
    provider = _BlockingProvider()
    await registry.register(provider)

    loop_tick = asyncio.Event()

    async def ticker() -> None:
        await asyncio.sleep(0.01)
        loop_tick.set()

    ticker_task = asyncio.create_task(ticker())
    results = await registry.check_all(timeout_per_service=1.0)
    await ticker_task

    assert loop_tick.is_set()
    assert results[provider.service_name].status is HealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_health_checks_run_concurrently() -> None:
    registry = ServiceHealthRegistry(max_concurrency=2)
    providers = [_BlockingProvider(0.08) for _ in range(2)]
    for index, provider in enumerate(providers):
        provider.service_name = f"blocking-{index}"
        await registry.register(provider)

    started = time.monotonic()
    results = await registry.check_all(timeout_per_service=1.0)
    elapsed = time.monotonic() - started

    assert elapsed < 0.14, f"health checks did not overlap: {elapsed:.3f}s"
    assert all(report.status is HealthStatus.HEALTHY for report in results.values())


@pytest.mark.asyncio
async def test_async_health_provider_remains_async_native() -> None:
    registry = ServiceHealthRegistry()
    provider = _AsyncProvider()
    await registry.register(provider)

    results = await registry.check_all(timeout_per_service=1.0)

    assert results[provider.service_name].status is HealthStatus.HEALTHY
