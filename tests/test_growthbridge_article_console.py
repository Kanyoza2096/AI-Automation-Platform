from pathlib import Path
from types import SimpleNamespace

import pytest

from routes import growthbridge_article_console as console


@pytest.mark.asyncio
async def test_growthbridge_article_preview_page_serves_credential_entry_shell(monkeypatch, tmp_path):
    template = tmp_path / "page.html"
    template.write_text("<body>__WORKSPACE_ID__ preview</body>", encoding="utf-8")
    monkeypatch.setattr(console, "_TEMPLATE", template)

    response = await console.growthbridge_article_preview_page(
        request=SimpleNamespace(),
    )

    assert response.status_code == 200
    assert b"__WORKSPACE_ID__" not in response.body
    assert b"preview" in response.body
    assert response.headers["cache-control"] == "no-store"
    assert "content-security-policy" in response.headers


@pytest.mark.asyncio
async def test_growthbridge_article_preview_page_fails_closed_without_template(monkeypatch, tmp_path):
    missing = tmp_path / "missing.html"
    monkeypatch.setattr(console, "_TEMPLATE", missing)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await console.growthbridge_article_preview_page(
            request=SimpleNamespace(),
        )
    assert exc.value.status_code == 500
