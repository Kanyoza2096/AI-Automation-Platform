from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from application.content_intelligence import (
    AnalyzeContentCommand,
    ContentIntelligenceApplicationService,
    ContentIntelligenceContext,
)

ROOT = Path(__file__).resolve().parents[2]


class FakePort:
    def __init__(self):
        self.calls = []

    async def analyze(self, topic, *, context="", max_results=8, fetch_top=4, workspace_id="platform"):
        self.calls.append((topic, context, max_results, fetch_top, workspace_id))
        return SimpleNamespace(to_dict=lambda: {"topic": topic, "workspace_id": workspace_id})


@pytest.mark.asyncio
async def test_content_intelligence_use_case_preserves_authenticated_workspace():
    port = FakePort()
    service = ContentIntelligenceApplicationService(port)
    result = await service.analyze(
        ContentIntelligenceContext(" ws-1 "),
        AnalyzeContentCommand("  climate risk  ", context="ops", max_results=5, fetch_top=2),
    )
    assert result.to_dict()["workspace_id"] == "ws-1"
    assert port.calls == [("climate risk", "ops", 5, 2, "ws-1")]


@pytest.mark.asyncio
async def test_content_intelligence_rejects_empty_workspace_before_port_call():
    port = FakePort()
    service = ContentIntelligenceApplicationService(port)
    with pytest.raises(ValueError, match="workspace scope"):
        await service.analyze(ContentIntelligenceContext(" "), AnalyzeContentCommand("topic"))
    assert port.calls == []


def test_application_layer_has_no_http_or_service_imports():
    tree = ast.parse((ROOT / "application" / "content_intelligence.py").read_text())
    imports = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert not any(name == "fastapi" or name.startswith("services") for name in imports)


def test_content_intelligence_route_delegates_to_application_service():
    source = (ROOT / "api" / "content_intelligence_automation.py").read_text()
    assert "ContentIntelligenceApplicationService" in source
    assert "service.analyze(" in source
    assert "content_intelligence_engine.analyze(" not in source


def test_adapter_is_the_infrastructure_seam():
    source = inspect.getsource(__import__("services.content_intelligence_adapter", fromlist=["ContentIntelligenceAdapter"]))
    assert "ContentIntelligenceEngine" in source
    assert "fastapi" not in source.lower()
