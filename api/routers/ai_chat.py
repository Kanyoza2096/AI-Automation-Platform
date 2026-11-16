from fastapi import APIRouter, Depends, Request, HTTPException
import logging

from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *
from config.settings import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Conversational roles are deliberately prompt-only. They do not grant tools,
# capabilities, evidence access, or permission to execute operational actions.
_ENGINE_GUIDANCE = {
    "general": "You are the KDO general intelligence assistant. Be accurate and transparent.",
    "director": "You are the KDO Director conversational advisor. Help clarify objectives, reason about strategy, and draft advisory plans. You cannot inspect live workspace evidence or stage/execute missions in this conversation.",
    "guardian": "You are the KDO Guardian conversational advisor. Explain security and risk concepts and help structure investigations. You cannot run scans, inspect live logs, or change security controls in this conversation.",
    "council": "You are the KDO Council deliberation assistant. Compare options, surface trade-offs, and prepare balanced recommendations. You cannot cast votes or create authoritative Council decisions.",
    "ai_brain": "You are the KDO AI Brain conversational explainer. Explain intelligence architecture and reason from information provided in this conversation. Do not claim access to live system memory or telemetry.",
    "content": "You are the KDO Content Engine writing assistant. Help develop, edit, and critique content. You cannot publish or schedule content.",
}

@router.post("/ai/chat")
async def ai_chat(
    body: AIChatRequest,
    request: Request,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _rate=create_rate_limit_dependency("ai_chat", max_per_minute=settings.api_rate_limit_ai_chat_per_user),
):
    """Safe multi-turn conversation through the provider-neutral LLM Gateway.

    This endpoint is conversational only: it never parses intents, invokes
    CommandExecutor, runs plugins, or performs operational actions. All actions
    must use their separately governed KCPG command contracts.
    """
    try:
        if INJECTION_PATTERNS_RE.search(body.message):
            cid = correlation_id_var.get()
            logger.warning("[%s] A03: Prompt injection blocked (conversation)", cid)
            return _ok({"reply": "I can't process that request. Please rephrase your message.", "model": "guard", "tokens_used": 0, "engine": "general"})

        context = body.context if isinstance(body.context, dict) else {}
        engine = str(context.get("engine") or "general").strip().lower()
        if engine not in _ENGINE_GUIDANCE:
            raise HTTPException(status_code=422, detail="Unsupported conversation engine")

        history = context.get("history", body.history)
        if not isinstance(history, list):
            history = []
        turns = []
        for item in history[-12:]:
            if not isinstance(item, dict):
                continue
            role = "assistant" if item.get("role") in ("assistant", "bot") else "user"
            content = str(item.get("content") or "").strip()
            if content:
                turns.append(f"{role.upper()}: {content[:2000]}")

        prompt = (
            f"{_ENGINE_GUIDANCE[engine]}\\n\\n"
            "Safety boundary: You are responding in a conversation only. You have no tools, "
            "no live telemetry or workspace evidence unless explicitly included in the user message, "
            "and no authority to execute commands. Never claim an action was performed. If an action "
            "is requested, explain that it must be submitted through the appropriate governed KDO control.\\n\\n"
            + ("Conversation so far:\\n" + "\\n".join(turns) + "\\n\\n" if turns else "")
            + f"USER: {body.message}\\nASSISTANT:"
        )
        from ai.gateway import get_llm_gateway
        gateway = get_llm_gateway()
        workspace_id = getattr(getattr(request, "state", None), "workspace_id", None)
        if not workspace_id:
            raise HTTPException(status_code=403, detail="Authenticated workspace context required")
        result = await gateway.complete_async(
            prompt,
            workspace_id=workspace_id,
            capability="chat",
            temperature=getattr(settings, "gemini_chat_temperature", 0.7),
            max_tokens=2048,
            caller="ai_conversation",
        )
        if not result.ok:
            raise HTTPException(status_code=503, detail="AI conversation provider temporarily unavailable")
        reply = result.text
        await append_audit(
            audit_log, "ai_chat", result.provider or "ai_provider", status_str="success",
            detail=f"Conversation engine={engine} ({len(body.message)}→{len(reply)} chars)",
        )
        return _ok({
            "reply": reply,
            "model": result.model or "ai_provider",
            "provider": result.provider,
            "engine": engine,
            "tokens_used": (getattr(result.ai_receipt.usage, "total_tokens", None) if result.ai_receipt and result.ai_receipt.usage else len(reply.split())),
            "request_id": result.request_id,
            "conversation_persistence": "client_context_only",
            "execution_authority": "none",
        })
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("AI conversation failed: %s", exc)
        raise HTTPException(status_code=503, detail="AI conversation service temporarily unavailable")
