"""
GitHub Connector Plugin Public Routes.

HARDEN: HMAC signature required; delivery idempotency; no accept-all in production.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("kanyoza.github.webhook")

router = APIRouter(prefix="/api/v1/github", tags=["github-connector-public"])


@router.post("/webhook")
async def webhook(request: Request):
    """Receive GitHub webhooks with signature + replay protection."""
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256") or request.headers.get(
        "x-hub-signature-256", ""
    )
    delivery_id = (
        request.headers.get("X-GitHub-Delivery")
        or request.headers.get("x-github-delivery")
        or ""
    )
    event = request.headers.get("X-GitHub-Event") or request.headers.get("x-github-event") or ""

    from core.webhook_security import verify_github_signature, webhook_idempotency

    ok, reason = verify_github_signature(body, signature)
    if not ok:
        logger.warning("GitHub webhook rejected: %s event=%s delivery=%s", reason, event, delivery_id[:36])
        raise HTTPException(status_code=403, detail=f"Webhook rejected: {reason}")

    if delivery_id and not webhook_idempotency.claim(f"github:{delivery_id}"):
        logger.info("GitHub webhook replay ignored delivery=%s", delivery_id[:36])
        return JSONResponse({"ok": True, "duplicate": True, "delivery_id": delivery_id})

    try:
        import json

        data = json.loads(body.decode("utf-8") or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if isinstance(data, dict):
        data.setdefault("_github_event", event)
        data.setdefault("_github_delivery", delivery_id)

    try:
        from plugins.github_connector.plugin import github_connector_plugin

        if github_connector_plugin:
            # Prefer async handler if present
            if hasattr(github_connector_plugin, "handle_webhook_async"):
                result = await github_connector_plugin.handle_webhook_async(data)
            else:
                result = github_connector_plugin.handle_webhook(data)
            webhook_idempotency.complete(f"github:{delivery_id}") if delivery_id else None
            return result if result is not None else {"ok": True}
        return JSONResponse({"ok": False, "error": "Plugin not running"}, status_code=503)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("GitHub webhook handler failed")
        raise HTTPException(status_code=500, detail="Webhook processing failed") from e
