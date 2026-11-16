from fastapi import APIRouter, Depends, Request, HTTPException
import asyncio
import logging
import uuid
from datetime import datetime, timezone

from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *
from config.settings import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# These are conversational personas, not privileged engine adapters. They do
# not grant tools, operational evidence access, or execution authority.
_ENGINE_GUIDANCE = {
    "general": "You are the KDO general intelligence assistant. Be accurate and transparent.",
    "director": "You are the KDO Director conversational advisor. Help clarify objectives, reason about strategy, and draft advisory plans. You cannot inspect live workspace evidence or stage/execute missions in this conversation.",
    "guardian": "You are the KDO Guardian conversational advisor. Explain security and risk concepts and help structure investigations. You cannot run scans, inspect live logs, or change security controls in this conversation.",
    "council": "You are the KDO Council deliberation assistant. Compare options, surface trade-offs, and prepare balanced recommendations. You cannot cast votes or create authoritative Council decisions.",
    "ai_brain": "You are the KDO AI Brain conversational explainer. Explain intelligence architecture and reason from information provided in this conversation. Do not claim access to live system memory or telemetry.",
    "content": "You are the KDO Content Engine writing assistant. Help develop, edit, and critique content. You cannot publish or schedule content.",
}
_VALID_ENGINES = set(_ENGINE_GUIDANCE)


def _conversation_db():
    from services.supabase import supabase_service
    client = supabase_service.service_client
    if client is None:
        raise HTTPException(status_code=503, detail="Persistent conversation storage is unavailable")
    return client


async def _db_call(operation):
    try:
        return await asyncio.to_thread(operation)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Persistent AI conversation database operation failed")
        raise HTTPException(status_code=503, detail="Persistent conversation storage is temporarily unavailable") from exc


def _rows(result):
    return list(getattr(result, "data", None) or [])


@router.post("/ai/chat")
async def ai_chat(
    body: AIChatRequest,
    request: Request,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _rate=create_rate_limit_dependency("ai_chat", max_per_minute=settings.api_rate_limit_ai_chat_per_user),
):
    """Workspace-isolated persistent conversation endpoint.

    context.operation supports list/load/chat. All database ownership comes
    from the authenticated request context; caller-supplied workspace IDs and
    client-provided conversation history are never used as authority.
    """
    if INJECTION_PATTERNS_RE.search(body.message):
        cid = correlation_id_var.get()
        logger.warning("[%s] A03: Prompt injection blocked (conversation)", cid)
        return _ok({"reply": "I can't process that request. Please rephrase your message.", "model": "guard", "tokens_used": 0, "engine": "general", "execution_authority": "none"})

    context = body.context if isinstance(body.context, dict) else {}
    operation = str(context.get("operation") or "chat").strip().lower()
    if operation not in {"list", "load", "chat"}:
        raise HTTPException(status_code=422, detail="Unsupported conversation operation")
    engine = str(context.get("engine") or "general").strip().lower()
    if engine not in _VALID_ENGINES:
        raise HTTPException(status_code=422, detail="Unsupported conversation engine")

    workspace_id = await require_workspace(request)
    db = _conversation_db()
    conversation_id = str(context.get("conversation_id") or "").strip()

    if operation == "list":
        result = await _db_call(lambda: db.table("ai_conversations")
            .select("id,engine,title,created_at,updated_at")
            .eq("workspace_id", workspace_id)
            .order("updated_at", desc=True)
            .limit(50)
            .execute())
        return _ok({"conversations": _rows(result), "persistence": "supabase", "execution_authority": "none"})

    if operation == "load":
        if not conversation_id or len(conversation_id) > 64:
            raise HTTPException(status_code=422, detail="A valid conversation_id is required")
        conv_result = await _db_call(lambda: db.table("ai_conversations")
            .select("id,engine,title,created_at,updated_at")
            .eq("id", conversation_id)
            .eq("workspace_id", workspace_id)
            .limit(1)
            .execute())
        conversations = _rows(conv_result)
        if not conversations:
            raise HTTPException(status_code=404, detail="Conversation not found")
        messages_result = await _db_call(lambda: db.table("ai_conversation_messages")
            .select("id,role,content,provider,model,created_at")
            .eq("conversation_id", conversation_id)
            .eq("workspace_id", workspace_id)
            .order("created_at")
            .limit(200)
            .execute())
        return _ok({"conversation": conversations[0], "messages": _rows(messages_result), "persistence": "supabase", "execution_authority": "none"})

    if INJECTION_PATTERNS_RE.search(body.message):
        raise HTTPException(status_code=422, detail="Message contains prohibited patterns")
    now = datetime.now(timezone.utc).isoformat()
    if conversation_id:
        conv_result = await _db_call(lambda: db.table("ai_conversations")
            .select("id,engine,title")
            .eq("id", conversation_id)
            .eq("workspace_id", workspace_id)
            .limit(1)
            .execute())
        conversations = _rows(conv_result)
        if not conversations:
            raise HTTPException(status_code=404, detail="Conversation not found")
        conversation = conversations[0]
        engine = str(conversation.get("engine") or engine)
    else:
        conversation_id = str(uuid.uuid4())
        title = body.message[:96].strip() or "New conversation"
        creator = auth_user_email_var.get() or "authenticated"
        conversation = {
            "id": conversation_id,
            "workspace_id": workspace_id,
            "created_by": creator[:256],
            "engine": engine,
            "title": title,
            "created_at": now,
            "updated_at": now,
        }
        await _db_call(lambda: db.table("ai_conversations").insert(conversation).execute())

    prior_result = await _db_call(lambda: db.table("ai_conversation_messages")
        .select("role,content")
        .eq("conversation_id", conversation_id)
        .eq("workspace_id", workspace_id)
        .order("created_at", desc=True)
        .limit(12)
        .execute())
    prior = list(reversed(_rows(prior_result)))
    turns = []
    for item in prior:
        role = "assistant" if item.get("role") == "assistant" else "user"
        content = str(item.get("content") or "").strip()
        if content:
            turns.append(f"{role.upper()}: {content[:2000]}")

    prompt = (
        f"{_ENGINE_GUIDANCE[engine]}\n\n"
        "Safety boundary: You are responding in a conversation only. You have no tools, "
        "no live telemetry or workspace evidence unless explicitly included in the user message, "
        "and no authority to execute commands. Never claim an action was performed. If an action "
        "is requested, explain that it must be submitted through the appropriate governed KDO control.\n\n"
        + ("Conversation so far:\n" + "\n".join(turns) + "\n\n" if turns else "")
        + f"USER: {body.message}\nASSISTANT:"
    )
    from ai.gateway import get_llm_gateway
    gateway = get_llm_gateway()
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
    message_time = datetime.now(timezone.utc).isoformat()
    user_message = {
        "id": str(uuid.uuid4()), "conversation_id": conversation_id,
        "workspace_id": workspace_id, "role": "user", "content": body.message,
        "created_at": message_time,
    }
    assistant_message = {
        "id": str(uuid.uuid4()), "conversation_id": conversation_id,
        "workspace_id": workspace_id, "role": "assistant", "content": reply,
        "provider": result.provider, "model": result.model,
        "tokens_used": (getattr(result.ai_receipt.usage, "total_tokens", None) if result.ai_receipt and result.ai_receipt.usage else len(reply.split())),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await _db_call(lambda: db.table("ai_conversation_messages").insert([user_message, assistant_message]).execute())
    await _db_call(lambda: db.table("ai_conversations").update({"updated_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", conversation_id).eq("workspace_id", workspace_id).execute())

    await append_audit(
        audit_log, "ai_chat", result.provider or "ai_provider", status_str="success",
        detail=f"Persistent conversation engine={engine}; conversation_id={conversation_id}",
    )
    return _ok({
        "reply": reply,
        "model": result.model or "ai_provider",
        "provider": result.provider,
        "engine": engine,
        "tokens_used": assistant_message["tokens_used"],
        "request_id": result.request_id,
        "conversation_id": conversation_id,
        "conversation_persistence": "supabase_workspace_scoped",
        "execution_authority": "none",
    })
