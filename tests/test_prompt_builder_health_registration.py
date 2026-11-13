import asyncio
from unittest.mock import AsyncMock, call

import lifecycle
from services.content_engine import content_engine
from services.prompt_builder import prompt_builder


def test_prompt_builder_exposes_canonical_health_provider_identity():
    assert prompt_builder.service_name == "prompt_builder"
    assert callable(prompt_builder.health_check)


def test_prompt_builder_health_check_reports_operational_contract():
    report = prompt_builder.health_check()

    assert report["service"] == "prompt_builder"
    assert report["status"] in {"healthy", "degraded", "unknown"}


def test_content_engine_boot_registers_content_engine_and_prompt_builder(monkeypatch):
    register = AsyncMock()
    monkeypatch.setattr(lifecycle.health_registry, "register", register)
    monkeypatch.setattr(content_engine, "set_container", lambda _container: None)

    asyncio.run(lifecycle._boot_content_engine(object()))

    assert register.await_args_list == [
        call(content_engine),
        call(prompt_builder),
    ]
