from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
import logging

from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

from config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# AI CHAT — A03 Prompt Injection Guard
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/ai/chat")
async def ai_chat(
    body: AIChatRequest,
    request: Request,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _rate=create_rate_limit_dependency("ai_chat", max_per_minute=settings.api_rate_limit_ai_chat_per_user),
):
    """
    A03: AI chat with defense-in-depth prompt injection protection.
    Guard applied at Pydantic validator AND here for redundancy.
    """
    try:
        if INJECTION_PATTERNS_RE.search(body.message):
            cid = correlation_id_var.get()
            logger.warning("[%s] A03: Prompt injection blocked (defense-in-depth)", cid)
            return _ok({
                "reply": "I can't process that request. Please rephrase your message.",
                "model": "guard",
                "tokens_used": 0,
            })

        if body.history:
            from core.memory import memory as _mem
            for turn in body.history[-10:]:
                role = str(turn.get("role", "user"))
                content = str(turn.get("content", ""))
                await _async_wrap(
                    _mem.add_message,
                    "api_chat_user",
                    role if role != "bot" else "assistant",
                    content,
                )

        from services.command_executor import CommandExecutor
        from services.gemini import GeminiService

        # Pass the FastAPI container so CommandExecutor can invoke plugins
        # without needing Flask's current_app (spec §10)
        # Propagate the caller's credential. A user-originated AI command must
        # never be upgraded to platform/master authority by an internal token.
        caller_token = ""
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            caller_token = authorization.split(" ", 1)[1].strip()
        elif request.headers.get("X-API-Key"):
            caller_token = request.headers.get("X-API-Key", "").strip()

        executor = CommandExecutor(
            gemini_service=GeminiService(),
            master_token=settings.master_api_token,
            credential_token=caller_token,
            base_url=str(request.base_url).rstrip("/") + "/api/v1",
            container=container,
        )
        intent = await _async_wrap(executor.parse_intent, body.message)

        if intent and intent.get("action"):
            result = await _async_wrap(executor.execute, intent)
            await append_audit(
                audit_log, "ai_command", intent.get("action", "unknown"),
                status_str="success" if result.get("ok") else "failed",
                detail=f"Command: {body.message[:100]}",
            )
            return _ok({
                "reply": result.get("message", "Command processed."),
                "model": "command-executor",
                "tokens_used": 0,
                "command_result": result,
            })

        # All conversational inference goes through the provider-neutral AI Gateway.
        # Cloudflare Workers AI is selected by KDO policy when configured; callers
        # never depend directly on a vendor SDK/API.
        from ai.gateway import get_llm_gateway
        gateway = get_llm_gateway()
        result = await gateway.complete_async(
            body.message,
            workspace_id=getattr(getattr(request, "state", None), "workspace_id", None) or "platform",
            capability="chat",
            temperature=getattr(settings, "gemini_chat_temperature", 0.7),
            max_tokens=2048,
            caller="ai_chat",
        )
        if not result.ok:
            raise RuntimeError(result.reason or "AI provider unavailable")
        reply = result.text
        await append_audit(
            audit_log, "ai_chat", result.provider or "ai_provider", status_str="success",
            detail=f"Chat ({len(body.message)}→{len(reply)} chars)",
        )
        return _ok({
            "reply": reply,
            "model": result.model or "ai_provider",
            "provider": result.provider,
            "tokens_used": (getattr(result.ai_receipt.usage, "total_tokens", None) if result.ai_receipt and result.ai_receipt.usage else len(reply.split())),
            "request_id": result.request_id,
        })
    except Exception as exc:
        logger.warning("Gemini chat failed: %s", exc)
        return _ok({
            "reply": "The AI service is temporarily unavailable.",
            "model": "unavailable",
            "tokens_used": 0,
        })
