from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# MESSAGES
#
# INCIDENT-2026-10-16-messages:
#   Two read endpoints were un-scoped:
#     * GET /messages              — read from analytics.get_recent_messages,
#                                    which queried conversation_history with
#                                    no workspace filter. Every caller saw
#                                    the same recent-messages list.
#     * GET /messages/{sender_id}  — filtered only by client-supplied
#                                    sender_id, with no workspace filter.
#
#   Fixed by requiring workspace_id on the tracker method and applying
#   .eq("workspace_id", ...) to both queries.
#
#   POST /messages/reply was verified against services/action_kernel.py:
#   the facebook_message handler looks up the target social_account and
#   rejects when account.workspace_id != intent.workspace_id, so the
#   client-supplied social_account_id cannot be used for cross-tenant
#   impersonation. No change needed.
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/messages")
async def get_messages(
    request: Request,
    _auth: bool = Depends(require_auth),
    limit: int = 50,
):
    """Get recent messages for the caller's workspace.

    INCIDENT-2026-10-16-messages: previously called
    analytics.get_recent_messages(limit) which read conversation_history
    unscoped. Now resolves workspace from the request and passes it to
    the tracker, which filters server-side.
    """
    try:
        limit = min(200, max(1, limit))
        workspace_id = _resolve_workspace_from_request(request)
        if not workspace_id:
            raise HTTPException(status_code=403, detail="Authenticated workspace is required")

        from analytics.tracker import analytics
        msgs = await _async_wrap(
            analytics.get_recent_messages, limit=limit, workspace_id=workspace_id
        )
        return _ok({"messages": msgs, "count": len(msgs), "workspace_id": workspace_id})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/messages")


@router.post("/messages/reply")
async def send_message(
    body: MessageReplyRequest,
    workspace_id: str = Depends(require_workspace),
):
    """Queue a governed Facebook Messenger reply through the Action Kernel.

    Tenancy: workspace_id comes from require_workspace (auth-derived).
    The kernel's facebook_message handler verifies that the supplied
    social_account_id belongs to that workspace and refuses otherwise.
    """
    try:
        from services.action_kernel import build_default_action_kernel
        from services.supabase import supabase_service
        db = getattr(supabase_service, "client", None)
        if db is None:
            return _err("Durable action store unavailable; message not sent", 503)
        kernel = build_default_action_kernel(db)
        result = await kernel.execute(
            workspace_id=workspace_id,
            action_type="facebook_message",
            parameters={"social_account_id": body.social_account_id, "recipient_id": body.recipient_id, "message": body.text},
            risk_level="medium",
            source="api.messages.reply",
        )
        if result.get("status") != "succeeded":
            return _err({"message": "Message was not sent", "execution": result}, 409)
        return _ok({"sent": True, "recipient": body.recipient_id, "execution": result})
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/messages/reply")


@router.get("/messages/{sender_id}")
async def get_conversation(
    sender_id: str,
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """Get conversation history for a sender, scoped to the caller's workspace.

    Previously filtered only by sender_id (client-supplied path parameter),
    which either leaked across workspaces or allowed sender_id enumeration
    to read every workspace's history. Now scoped by workspace_id as well.
    """
    try:
        workspace_id = _resolve_workspace_from_request(request)
        if not workspace_id:
            raise HTTPException(status_code=403, detail="Authenticated workspace is required")

        from services.supabase import supabase_service
        if not (supabase_service and supabase_service.client):
            return _ok({"messages": [], "sender_id": sender_id, "note": "Supabase not configured"})

        result = await _async_wrap(
            lambda: supabase_service.client.table("conversation_history")
            .select("*")
            .eq("workspace_id", workspace_id)
            .eq("sender_id", sender_id)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        return _ok({"messages": result.data or [], "sender_id": sender_id, "workspace_id": workspace_id})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/messages/{sender_id}")
