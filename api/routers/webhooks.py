from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# WEBHOOKS — A08 Signature Validation
# ═══════════════════════════════════════════════════════════════════════════

@router.api_route("/webhooks/facebook", methods=["GET", "POST"])
async def facebook_webhook(
    request: Request,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
):
    """A08: Facebook webhook with HMAC-SHA256 verification."""
    if request.method == "GET":
        mode = request.query_params.get("hub.mode")
        token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge", "")
        if mode == "subscribe":
            container = request.app.state.container
            from repositories.social_account_repository import SocialAccountRepository
            try:
                repo = await _async_wrap(container.resolve, SocialAccountRepository)
                accounts = await _async_wrap(repo.find_by_platform, "facebook")
                for acc in accounts:
                    pc = getattr(acc, "platform_config", {}) or {}
                    if pc.get("verify_token") == token:
                        return Response(content=challenge, status_code=200)
            except Exception:
                pass
        return Response(content="Verification failed", status_code=403)

    if not await _verify_webhook_signature(request):
        return Response(content="Invalid signature", status_code=403)

    payload = await request.json()
    logger.info("Facebook webhook: %s", str(payload)[:300])
    await append_audit(
        audit_log, "webhook_facebook", "webhook:facebook", status_str="success",
    )
    return Response(content="OK", status_code=200)


@router.post("/webhooks/twitter")
async def twitter_webhook(
    request: Request,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
):
    """A08: Twitter webhook with signature verification."""
    if not await _verify_webhook_signature(request):
        return Response(content="Invalid signature", status_code=403)
    await append_audit(
        audit_log, "webhook_twitter", "webhook:twitter", status_str="success",
    )
    return Response(content="OK", status_code=200)


@router.post("/webhooks/linkedin")
async def linkedin_webhook(
    request: Request,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
):
    """A08: LinkedIn webhook with signature verification."""
    if not await _verify_webhook_signature(request):
        return Response(content="Invalid signature", status_code=403)
    await append_audit(
        audit_log, "webhook_linkedin", "webhook:linkedin", status_str="success",
    )
    return Response(content="OK", status_code=200)


@router.api_route("/webhooks/instagram", methods=["GET", "POST"])
async def instagram_webhook(request: Request):
    """A08: Instagram webhook with signature verification."""
    if request.method == "GET":
        if (
            request.query_params.get("hub.mode") == "subscribe"
            and request.query_params.get("hub.verify_token")
        ):
            return Response(
                content=request.query_params.get("hub.challenge", ""),
                status_code=200,
            )
        return Response(content="Verification failed", status_code=403)
    if not await _verify_webhook_signature(request):
        return Response(content="Invalid signature", status_code=403)
    return Response(content="OK", status_code=200)

