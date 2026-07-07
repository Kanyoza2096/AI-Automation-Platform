"""
Kanyoza Systems AI Platform v10 — Facebook Messenger Webhook Handler.

- HMAC signature verification (APP_SECRET)
- Rate limiting per sender
- Analytics tracking
- Domain event emission via EventBus
- Slash commands (/help, /status, /post, /ratelimit …) for the bot owner
- Bang commands (!post, !news, !pause …) for the bot owner
"""

import json
import logging
import threading
import time as time_module
from datetime import datetime, timezone
from typing import Optional
from flask import request, jsonify

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

_humanizer = Humanizer()

# ---------------------------------------------------------------------------
# Domain event helpers (fire-and-forget, never crash the request)
# ---------------------------------------------------------------------------

def _emit(event):
    try:
        from core.event_bus import get_event_bus
        get_event_bus().publish(event)
    except Exception:
        pass

# Slash-command handler — initialized lazily after services are ready
_slash_handler: Optional["_SlashCommands"] = None
_slash_handler_lock = threading.Lock()
_guardian_runner = None   # set by lifecycle.py via set_guardian_runner()


def set_guardian_runner(fn) -> None:
    """Called by lifecycle.py after GuardianService is started."""
    global _guardian_runner
    _guardian_runner = fn
    logger.info("Guardian runner wired to webhook slash handler.")


def _get_slash_handler() -> "_SlashCommands":
    global _slash_handler
    # Double-checked locking — prevents duplicate init in multi-threaded Flask
    if _slash_handler is None:
        with _slash_handler_lock:
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


def register_webhook_routes(app):
    """Register webhook routes."""

    @app.route("/webhook", methods=["GET"])
    def verify_webhook():
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == settings.verify_token:
            return challenge, 200
        return "Verification failed", 403

    @app.route("/webhook", methods=["POST"])
    def receive_message():
        if not verify_facebook_signature(request.data, request.headers.get('X-Hub-Signature-256', ''), settings.app_secret):
            return "Invalid signature", 403

        data = request.get_json(silent=True)
        if not data:
            logger.warning("Webhook POST received with no parseable JSON body")
            return "EVENT_RECEIVED", 200

        # ── Live Payload Inspector: emit sanitized inbound webhook payload ───
        try:
            from core.socket_server import emit_to_dashboard
            # Build a schema-safe summary — never emit raw PSIDs or message text.
            _entries = data.get("entry", [])
            _event_count = sum(len(e.get("messaging", [])) for e in _entries)
            _event_types = list({
                next(iter({k for k in ev if k not in ("sender", "recipient", "timestamp")}), "unknown")
                for e in _entries
                for ev in e.get("messaging", [])
            })
            emit_to_dashboard("payload_inbound", {
                "direction": "inbound",
                "source": "facebook_webhook",
                "ts": datetime.now(timezone.utc).isoformat(),
                "object": data.get("object", ""),
                "entry_count": len(_entries),
                "event_count": _event_count,
                "event_types": _event_types,
                "size_bytes": len(request.data),
            })
        except Exception:
            pass
        # ─────────────────────────────────────────────────────────────────────

        try:
            for entry in data.get("entry", []):
                for event in entry.get("messaging", []):
                    threading.Thread(target=_handle_event, args=(event,), daemon=True).start()
        except Exception as e:
            logger.error(f"Webhook dispatch error: {e}")
        return "EVENT_RECEIVED", 200


def _handle_event(event: dict):
    _t0 = time_module.monotonic()
    _ok = True
    try:
        sender_id = event.get("sender", {}).get("id")
        message = event.get("message", {})
        if not sender_id:
            return
        if "text" in message:
            _handle_text_message(sender_id, message["text"].strip())
        elif "attachments" in message:
            _handle_attachments(sender_id, message["attachments"])
        elif "postback" in event:
            _handle_postback(sender_id, event["postback"])
    except Exception as e:
        _ok = False
        logger.error(f"Event handling error: {e}")
    finally:
        metrics.record_webhook((time_module.monotonic() - _t0) * 1000, _ok)


def _handle_text_message(sender_id: str, text: str):
    logger.info(f"📩 Message from {sender_id}: '{text[:80]}'")

    text = sanitize_message(text)
    if not text:
        logger.warning(f"Empty after sanitize for {sender_id}")
        return

    if not rate_limiter.is_allowed(sender_id, 'message_per_user'):
        logger.warning(f"Rate limited: {sender_id}")
        facebook.send_message(sender_id, "You're sending messages too quickly. Please wait a moment.")
        return

    if detect_abuse(text):
        logger.warning(f"Abuse detected: {sender_id}")
        facebook.send_message(sender_id, "I don't respond to that kind of message.")
        return

    analytics.record_message_received(sender_id)

    # Emit domain event
    from core.events import MessageReceivedEvent
    _emit(MessageReceivedEvent(sender_id=sender_id, text=text))

    if sender_id == settings.owner_psid:
        # Slash commands (from owner_commands.py — /help, /post, /status …)
        if text.startswith("/"):
            try:
                slash = _get_slash_handler()
                if slash.is_owner(sender_id, settings):
                    slash.handle(sender_id, text)
                    logger.info("Slash command handled for %s", sender_id)
                    return
            except Exception as exc:
                logger.error("Slash command error: %s", exc)

        # Bang commands (from owner.py — !post, !news, !pause …)
        if owner_commands.handle_command(sender_id, text):
            logger.info("Owner command handled for %s", sender_id)
            return

    logger.info(f"Starting async processing for {sender_id}")
    facebook.send_mark_seen(sender_id)
    threading.Thread(target=_process_message_async, args=(sender_id, text), daemon=True).start()


def _process_message_async(sender_id: str, text: str):
    logger.info(f"⚙️ Processing message from {sender_id}: '{text[:50]}'")
    start_time = time_module.time()

    try:
        language = detect_language(text)
        logger.info(f"Language: {language}")

        memory.update_user_profile(sender_id, {'communication_style': language})
        sentiment = detect_sentiment(text)
        logger.info(f"Sentiment: {sentiment}")

        memory.extract_and_save_facts(sender_id, text)

        delay = calculate_typing_delay(text, min(len(text.split()) * 2, 200))
        logger.info(f"Waiting {delay:.1f}s before replying")
        time_module.sleep(delay)

        facebook.send_typing_on(sender_id)
        logger.info("Calling Gemini for response...")
        reply = gemini.generate_chat_response(sender_id, text, language, sentiment)
        logger.info(f"Gemini replied: '{reply[:80]}...'")
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
                    logger.info(f"✅ Split reply sent to {sender_id}")
                else:
                    logger.warning(
                        "⚠️ Split reply to %s — part1_ok=%s part2_ok=%s "
                        "(check PAGE_ACCESS_TOKEN / page_id config)",
                        sender_id, ok1, ok2,
                    )
                return

        ok = facebook.send_message(sender_id, reply)
        if ok:
            logger.info(f"✅ Reply sent to {sender_id} ({response_time:.1f}s)")
        else:
            logger.warning(
                "⚠️ Reply NOT sent to %s — facebook.send_message returned False. "
                "facebook.is_configured=%s  (check PAGE_ACCESS_TOKEN / PAGE_ID env vars)",
                sender_id, facebook.is_configured,
            )

    except Exception as e:
        logger.error(f"❌ Message processing failed: {type(e).__name__}: {e}")
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
