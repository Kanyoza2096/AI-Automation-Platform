from pathlib import Path
from types import SimpleNamespace

import pytest

from routes import growthbridge_article_console as console


@pytest.mark.asyncio
async def test_growthbridge_article_preview_page_is_workspace_scoped(monkeypatch, tmp_path):
    template = tmp_path / "page.html"
    template.write_text('<body data-workspace-id="__WORKSPACE_ID__">preview</body>', encoding="utf-8")
    monkeypatch.setattr(console, "_TEMPLATE", template)

    async def workspace(_request):
        return 'workspace-"safe'

    monkeypatch.setattr(console, "require_workspace", workspace)
    response = await console.growthbridge_article_preview_page(
        request=SimpleNamespace(), _auth=True,
    )

    assert response.status_code == 200
    assert 'data-workspace-id="workspace-&quot;safe"' in response.body.decode()
    assert response.headers["cache-control"] == "no-store"
    assert "content-security-policy" in response.headers


@pytest.mark.asyncio
async def test_growthbridge_article_preview_page_fails_closed_without_template(monkeypatch, tmp_path):
    missing = tmp_path / "missing.html"
    monkeypatch.setattr(console, "_TEMPLATE", missing)

    async def workspace(_request):
        return "workspace-1"

    monkeypatch.setattr(console, "require_workspace", workspace)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await console.growthbridge_article_preview_page(
            request=SimpleNamespace(), _auth=True,
        )
    assert exc.value.status_code == 500
