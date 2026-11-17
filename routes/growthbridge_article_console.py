"""Authenticated GrowthBridge article-preview operator page.

The page only invokes the existing workspace-scoped dry-run API. It has no
publish or scheduling controls and never receives publishing authority.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["GrowthBridge Article Preview"])
_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "growthbridge_article_preview.html"


@router.get("/command-center/growthbridge-articles", response_class=HTMLResponse, include_in_schema=False)
@router.get("/growthbridge/article-preview", response_class=HTMLResponse, include_in_schema=False)
async def growthbridge_article_preview_page(request: Request):
    """Serve the credential-entry shell; all data and actions remain authenticated APIs."""
    try:
        template = _TEMPLATE.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail="growthbridge_article_console_unavailable") from exc
    document = template.replace("__WORKSPACE_ID__", "")
    return HTMLResponse(
        document,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        },
    )
