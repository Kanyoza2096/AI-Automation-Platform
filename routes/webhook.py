"""
Kanyoza Systems AI Platform v11 — Facebook Messenger Webhook Handler (FastAPI).

- HMAC signature verification (APP_SECRET)
- Rate limiting per sender
- Analytics tracking
- Domain event emission via EventBus
- Slash commands (/help, /status, /post, /ratelimit …) for the bot owner
- Bang commands (!post, !news, !pause …) for the bot owner
"""

import json
import logging
import asyncio
import time as time_module
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, Query
from fastapi.responses import PlainTextResponse, JSONResponse

from config.settings import settings
from services.facebook import facebook
from services.gemini import gemini
from core.memory import memory
from core.language import detect_language
from core.sentiment import detect_sentiment
from core.humanizer import Humanizer
from analytics.tracker import analytics
from routes.owner import owner_commands
from routes.owner_commands import OwnerCommands as _SlashCommands
from utils.security import verify_facebook_signature, sanitize_message, detect_abuse
from utils.rate_limiter import rate_limiter
from utils.metrics import metrics

logger = logging.getLogger("kanyoza.routes.webhook")

router = APIRouter(tags=["webhooks"])

_humanizer = Humanizer()

# ---------------------------------------------------------------------------
# Domain event helpers
# ---------------------------------------------------------------------------

def _emit(event):
    try:
        from core.event_bus import get_event_bus
        get_event_bus().publish(event)
    except Exception:
        pass

def _emit_async(event_name: str, data: dict):
    """Schedule an async SocketIO emit without blocking."""
    try:
        from core.socket_server import emit_to_dashboard
        loop = asyncio.get_running_loop()
        asyncio.create_task(emit_to_dashboard(event_name, data))
    except RuntimeError:
        pass  # No running event loop — skip emit (background thread context)
    except Exception:
        pass

# Slash-command handler
_slash_handler: Optional["_SlashCommands"] = None
_guardian_runner = None


def set_guardian_runner(fn) -> None:
    """Called by lifecycle.py after GuardianService is started."""
    global _guardian_runner
    _guardian_runner = fn
    logger.info("Guardian runner wired to webhook slash handler.")


def _get_slash_handler() -> "_SlashCommands":
    global _slash_handler
    if _slash_handler is None:
        from services.facebook import facebook as _fb
        from analytics.tracker import analytics as _analytics
        from scheduler.post_scheduler import scheduler as _sched
        from config.settings import settings as _settings
        _slash_handler = _SlashCommands(
            facebook_service=_fb,
            analytics=_analytics,
            post_scheduler=_sched,
            guardian_runner=_guardian_runner,
            settings=_settings,
        )
    return _slash_handler


def calculate_typing_delay(msg, length):
    return _humanizer.pick_response_delay(length)


def should_split_message(msg):
    return len(msg) > 400


# ── WhatsApp Cloud API webhook reconciliation ─────────────────────────────
@router.get("/webhook/whatsapp", operation_id="whatsapp_webhook_verify")
async def verify_whatsapp_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Verify a WhatsApp Cloud API webhook subscription.

    The verify token is intentionally separate from provider access tokens.
    """
    import os
    expected = (os.getenv("WHATSAPP_VERIFY_TOKEN") or getattr(settings, "verify_token", "")).strip()
    if hub_mode == "subscribe" and expected and hub_verify_token == expected:
        return PlainTextResponse(content=str(hub_challenge or ""), status_code=200)
    return PlainTextResponse(content="Verification failed", status_code=403)


@router.post("/webhook/whatsapp", operation_id="whatsapp_webhook_receive")
async def receive_whatsapp_webhook(request: Request):
    """Consume WhatsApp status webhooks as authoritative publish evidence."""
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "") or request.headers.get("x-hub-signature-256", "")
    try:
        from core.webhook_security import verify_facebook_app_secret, webhook_idempotency
        ok, reason = verify_facebook_app_secret(body, signature, getattr(settings, "app_secret", None))
        if not ok:
            logger.warning("WhatsApp webhook rejected: %s", reason)
            return PlainTextResponse(content="Invalid signature", status_code=403)
    except Exception:
        return PlainTextResponse(content="Webhook verification unavailable", status_code=503)

    try:
        data = json.loads(body.decode("utf-8") or "{}")
    except Exception:
        return PlainTextResponse(content="Invalid JSON", status_code=400)
    if data.get("object") != "whatsapp_business_account":
        return PlainTextResponse(content="Invalid object", status_code=400)

    from services.provider_reconciliation import ProviderReconciliationService
    reconciler = ProviderReconciliationService()
    processed = 0
    ignored = 0
    try:
        for entry in data.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                for status_event in value.get("statuses") or []:
                    message_id = str(status_event.get("id") or "").strip()
                    status = str(status_event.get("status") or "").strip().lower()
                    if not message_id or not status:
                        ignored += 1
                        continue
                    delivery_key = f"wa-status:{message_id}:{status}:{status_event.get('timestamp', '')}"
                    if not webhook_idempotency.claim(delivery_key):
                        ignored += 1
                        continue
                    evidence = status_event.get("errors") or status
                    result = reconciler.apply_event(
                        platform="whatsapp",
                        provider_post_id=message_id,
                        status=status,
                        evidence=json.dumps(evidence, separators=(",", ":")) if isinstance(evidence, (dict, list)) else str(evidence),
                    )
                    webhook_idempotency.complete(delivery_key)
                    processed += int(bool(result.get("matched")))
                    ignored += int(not result.get("matched"))
    except Exception as exc:
        logger.error("WhatsApp reconciliation webhook failed: %s", exc)
        return PlainTextResponse(content="Webhook processing failed", status_code=500)

    return JSONResponse({"ok": True, "processed": processed, "ignored": ignored})


# ═══════════════════════════════════════════════════════════════════════════
# WEBHOOK ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/webhook", operation_id="facebook_webhook_verify")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Facebook webhook verification endpoint."""
    if hub_mode == "subscribe" and hub_verify_token == settings.verify_token:
        return PlainTextResponse(content=str(hub_challenge or ""), status_code=200)
    return PlainTextResponse(content="Verification failed", status_code=403)


@router.post("/webhook", operation_id="facebook_webhook_receive")
async def receive_message(request: Request):
    """Receive and process Facebook Messenger webhook events."""
    import json as _json

    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "") or request.headers.get(
        "x-hub-signature-256", ""
    )

    # Prefer centralized fail-closed verifier
    try:
        from core.webhook_security import verify_facebook_app_secret, webhook_idempotency

        ok, reason = verify_facebook_app_secret(body, signature, getattr(settings, "app_secret", None))
        if not ok:
            logger.warning("Facebook webhook rejected: %s", reason)
            return PlainTextResponse(content="Invalid signature", status_code=403)
    except Exception:
        if not verify_facebook_signature(body, signature, settings.app_secret):
            return PlainTextResponse(content="Invalid signature", status_code=403)

    # Parse from body bytes (body already consumed — do not call request.json())
    try:
        data = _json.loads(body.decode("utf-8") or "{}") if body else {}
    except Exception:
        logger.warning("Webhook POST received with no parseable JSON body")
        return PlainTextResponse(content="Invalid JSON", status_code=400)

    if not data:
        return PlainTextResponse(content="Empty webhook payload", status_code=400)

    # Idempotency: Facebook mid / entry id fingerprint
    claimed_mids = []
    try:
        from core.webhook_security import webhook_idempotency
        for entry in data.get("entry") or []:
            for ev in entry.get("messaging") or []:
                mid = (ev.get("message") or {}).get("mid") or ""
                if not mid:
                    continue
                key = f"fb:{mid}"
                if webhook_idempotency.claim(key):
                    claimed_mids.append(key)
                else:
                    logger.info("Facebook webhook replay ignored mid=%s", mid[:40])
    except Exception as _idem_exc:
        logger.debug("webhook idempotency claim failed: %s", _idem_exc)

    # ── Live Payload Inspector ──────────────────────────────────────────
    try:
        entries = data.get("entry", [])
        event_count = sum(len(e.get("messaging", [])) for e in entries)
        event_types = list({
            next(iter({k for k in ev if k not in ("sender", "recipient", "timestamp")}), "unknown")
            for e in entries
            for ev in e.get("messaging", [])
        })
        _emit_async("payload_inbound", {
            "direction": "inbound",
            "source": "facebook_webhook",
            "ts": datetime.now(timezone.utc).isoformat(),
            "object": data.get("object", ""),
            "entry_count": len(entries),
            "event_count": event_count,
            "event_types": event_types,
            "size_bytes": len(body),
        })
    except Exception:
        pass

    # Process events in background
    try:
        claimed_set = set(claimed_mids)
        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                mid = (event.get("message") or {}).get("mid") or ""
                key = f"fb:{mid}" if mid else ""
                if mid and key not in claimed_set:
                    continue
                ok = _handle_event(event)
                if mid and ok:
                    webhook_idempotency.complete(key)
                elif mid and not ok:
                    logger.warning("Facebook event failed; durable claim remains retryable mid=%s", mid[:40])
    except Exception as e:
        logger.error("Webhook dispatch error: %s", e)
        return PlainTextResponse(content="Webhook processing failed", status_code=500)

    return PlainTextResponse(content="EVENT_RECEIVED", status_code=200)


# ═══════════════════════════════════════════════════════════════════════════
# EVENT HANDLERS
# ═══════════════════════════════════════════════════════════════════════════

def _handle_event(event: dict) -> bool:
    _t0 = time_module.monotonic()
    _ok = True
    try:
        sender_id = event.get("sender", {}).get("id")
        message = event.get("message", {})
        if not sender_id:
            return True
        if "text" in message:
            _handle_text_message(sender_id, message["text"].strip())
        elif "attachments" in message:
            _handle_attachments(sender_id, message["attachments"])
        elif "postback" in event:
            _handle_postback(sender_id, event["postback"])
    except Exception as e:
        _ok = False
        logger.error("Event handling error: %s", e)
    finally:
        metrics.record_webhook((time_module.monotonic() - _t0) * 1000, _ok)
    return _ok


def _handle_text_message(sender_id: str, text: str):
    logger.info("📩 Message from %s: '%s'", sender_id, text[:80])

    text = sanitize_message(text)
    if not text:
        logger.warning("Empty after sanitize for %s", sender_id)
        return

    if not rate_limiter.is_allowed(sender_id, 'message_per_user'):
        logger.warning("Rate limited: %s", sender_id)
        facebook.send_message(sender_id, "You're sending messages too quickly. Please wait a moment.")
        return

    if detect_abuse(text):
        logger.warning("Abuse detected: %s", sender_id)
        facebook.send_message(sender_id, "I don't respond to that kind of message.")
        return

    analytics.record_message_received(sender_id)

    # Emit domain event
    from core.events import MessageReceivedEvent
    _emit(MessageReceivedEvent(sender_id=sender_id, text=text))

    if sender_id == settings.owner_psid:
        # Slash commands
        if text.startswith("/"):
            try:
                slash = _get_slash_handler()
                if slash.is_owner(sender_id, settings):
                    slash.handle(sender_id, text)
                    logger.info("Slash command handled for %s", sender_id)
                    return
            except Exception as exc:
                logger.error("Slash command error: %s", exc)

        # Bang commands
        if owner_commands.handle_command(sender_id, text):
            logger.info("Owner command handled for %s", sender_id)
            return

    logger.info("Starting async processing for %s", sender_id)
    facebook.send_mark_seen(sender_id)
    from utils.executor import submit_bg_task
    submit_bg_task(_process_message_async, sender_id, text)


def _process_message_async(sender_id: str, text: str):
    logger.info("⚙️ Processing message from %s: '%s'", sender_id, text[:50])
    start_time = time_module.time()

    try:
        language = detect_language(text)
        logger.info("Language: %s", language)

        memory.update_user_profile(sender_id, {'communication_style': language})
        sentiment = detect_sentiment(text)
        logger.info("Sentiment: %s", sentiment)

        memory.extract_and_save_facts(sender_id, text)

        delay = calculate_typing_delay(text, min(len(text.split()) * 2, 200))
        logger.info("Waiting %.1fs before replying", delay)
        time_module.sleep(delay)

        facebook.send_typing_on(sender_id)
        reply = None
        try:
            from services.chat_routing import answer_with_tools_if_needed
            reply = answer_with_tools_if_needed(text, language=language)
            if reply:
                logger.info("Tool-agent reply for %s: '%s...'", sender_id, reply[:80])
        except Exception as _route_exc:
            logger.debug("chat routing skipped: %s", _route_exc)
        if not reply:
            logger.info("Calling Gemini for response...")
            reply = gemini.generate_chat_response(sender_id, text, language, sentiment)
            logger.info("Gemini replied: '%s...'", reply[:80])
        facebook.send_typing_off(sender_id)

        memory.add_message(sender_id, "user", text)
        memory.add_message(sender_id, "assistant", reply)
        memory.increment_message_count(sender_id)

        response_time = time_module.time() - start_time
        analytics.record_message_sent(sender_id, response_time)

        # Emit domain event
        from core.events import DomainEvent, EventType
        _emit(DomainEvent(event_type=EventType.MESSAGE_SENT,
                          payload={"sender_id": sender_id, "latency_s": response_time}))

        if should_split_message(reply):
            parts = _split_reply_naturally(reply)
            if len(parts) == 2:
                ok1 = facebook.send_message(sender_id, parts[0])
                time_module.sleep(3)
                facebook.send_typing_on(sender_id)
                time_module.sleep(2)
                facebook.send_typing_off(sender_id)
                ok2 = facebook.send_message(sender_id, parts[1])
                if ok1 and ok2:
                    logger.info("✅ Split reply sent to %s", sender_id)
                else:
                    logger.warning(
                        "⚠️ Split reply to %s — part1_ok=%s part2_ok=%s",
                        sender_id, ok1, ok2,
                    )
                return

        ok = facebook.send_message(sender_id, reply)
        if ok:
            logger.info("✅ Reply sent to %s (%.1fs)", sender_id, response_time)
        else:
            logger.warning(
                "⚠️ Reply NOT sent to %s — facebook.send_message returned False. "
                "facebook.is_configured=%s",
                sender_id, facebook.is_configured,
            )

    except Exception as e:
        logger.error("❌ Message processing failed: %s: %s", type(e).__name__, e)

        analytics.record_message_failed(sender_id, str(e))
        try:
            facebook.send_typing_off(sender_id)
            facebook.send_message(sender_id, "Ah, my connection is acting up. Give me a sec to retry?")
        except Exception:
            pass


def _split_reply_naturally(reply: str) -> list:
    if '\n\n' in reply:
        parts = reply.split('\n\n', 1)
        if len(parts[0]) > 50 and len(parts[1]) > 50:
            return parts
    sentences = reply.replace('\n', ' ').split('. ')
    if len(sentences) >= 3:
        mid = len(sentences) // 2
        part1 = '. '.join(sentences[:mid]) + '.'
        part2 = '. '.join(sentences[mid:])
        if len(part1) > 50 and len(part2) > 50:
            return [part1, part2]
    return [reply]


def _handle_attachments(sender_id: str, attachments: list):
    for att in attachments:
        att_type = att.get("type")
        if att_type == "image":
            facebook.send_message(sender_id, "I see the image! What would you like to know about it?")
        elif att_type == "audio":
            facebook.send_message(sender_id, "I received your voice message. Please type your message and I'll respond right away!")
        elif att_type == "video":
            facebook.send_message(sender_id, "I see you sent a video! Feel free to describe what's in it.")
        else:
            facebook.send_message(sender_id, "I received your attachment! What would you like to discuss?")


def _handle_postback(sender_id: str, postback: dict):
    payload = postback.get("payload", "")
    if payload == "get_started":
        greeting = (
            "👋 Hello! I'm a software engineer and entrepreneur from Malawi.\n\n"
            "Ask me about tech, software development, business, or just say hi. "
            "I'm here to chat, not to automate.\n\nWhat's on your mind?"
        )
        facebook.send_message(sender_id, greeting)
