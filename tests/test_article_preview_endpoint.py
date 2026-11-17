import json
from types import SimpleNamespace

import pytest

from api.routers import workflows as workflow_routes


class FakeContainer:
    def __init__(self, brand):
        self.brand = brand

    def resolve(self, kind):
        name = getattr(kind, "__name__", "")
        if name == "BrandRepository":
            return SimpleNamespace(find_by_id=lambda _brand_id: self.brand)
        if name == "KnowledgeRepository":
            return object()
        raise AssertionError(f"Unexpected dependency: {name}")


@pytest.mark.asyncio
async def test_article_preview_endpoint_is_workspace_scoped_and_dry_run(monkeypatch):
    brand = SimpleNamespace(
        id="brand-growthbridge",
        workspace_id="workspace-1",
        name="GrowthBridge Virtual",
    )
    container = FakeContainer(brand)
    calls = {}

    async def async_wrap(func, *args, **kwargs):
        return func(*args, **kwargs)

    async def workspace(_request):
        return "workspace-1"

    class FakeWorkflow:
        def __init__(self, **kwargs):
            calls["init"] = kwargs
            assert kwargs["dry_run"] is True

        async def execute_async(self, initial_context):
            calls["context"] = initial_context
            return SimpleNamespace(
                success=True,
                workflow_name="article_preview",
                total_ms=12.5,
                last_output={
                    "mode": "preview_only",
                    "publishable": False,
                    "title": "A grounded GrowthBridge article",
                    "citations": ["kb-1"],
                },
                failed_step=None,
                steps=[],
            )

    monkeypatch.setattr(workflow_routes, "_async_wrap", async_wrap)
    monkeypatch.setattr(workflow_routes, "require_workspace", workspace)
    monkeypatch.setattr(
        "workflows.article_workflow.ArticlePublishWorkflow", FakeWorkflow
    )

    response = await workflow_routes.preview_article(
        request=SimpleNamespace(),
        body={"brand_id": "brand-growthbridge", "category": "Digital"},
        container=container,
        _auth=True,
    )
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["status"] == "preview_ready"
    assert payload["preview"]["publishable"] is False
    assert payload["publish_attempted"] is False
    assert calls["init"]["dry_run"] is True
    assert calls["context"]["workspace_id"] == "workspace-1"
    assert calls["context"]["source"] == "manual_preview"


@pytest.mark.asyncio
async def test_article_preview_endpoint_rejects_cross_workspace_brand(monkeypatch):
    brand = SimpleNamespace(
        id="brand-other",
        workspace_id="workspace-2",
        name="Other brand",
    )

    async def async_wrap(func, *args, **kwargs):
        return func(*args, **kwargs)

    async def workspace(_request):
        return "workspace-1"

    monkeypatch.setattr(workflow_routes, "_async_wrap", async_wrap)
    monkeypatch.setattr(workflow_routes, "require_workspace", workspace)

    response = await workflow_routes.preview_article(
        request=SimpleNamespace(),
        body={"brand_id": "brand-other"},
        container=FakeContainer(brand),
        _auth=True,
    )

    assert response.status_code == 404
    assert json.loads(response.body)["ok"] is False


@pytest.mark.asyncio
async def test_article_preview_endpoint_requires_brand_id():
    response = await workflow_routes.preview_article(
        request=SimpleNamespace(),
        body={},
        container=FakeContainer(None),
        _auth=True,
    )

    assert response.status_code == 400
    assert "brand_id" in json.loads(response.body)["error"]
