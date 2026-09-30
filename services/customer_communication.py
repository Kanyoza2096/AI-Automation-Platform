"""Unified customer communication transport.

This module normalizes outbound customer communication across supported channels.
It is transport-only: callers must already be inside the Action Kernel boundary.
"""
from __future__ import annotations

import asyncio
import os
import smtplib
from email.message import EmailMessage
from typing import Any

SUPPORTED_CHANNELS = {"whatsapp", "email"}


def _smtp_config() -> dict[str, Any]:
    return {
        "host": os.getenv("SMTP_HOST", "").strip(),
        "port": int(os.getenv("SMTP_PORT", "587") or 587),
        "username": os.getenv("SMTP_USERNAME", "").strip(),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "from_address": os.getenv("SMTP_FROM_ADDRESS", "").strip(),
        "use_tls": os.getenv("SMTP_USE_TLS", "true").strip().lower() not in {"0", "false", "no"},
    }


def _send_email_sync(to: str, subject: str, message: str, html: bool = False) -> dict[str, Any]:
    cfg = _smtp_config()
    if not cfg["host"] or not cfg["username"] or not cfg["password"] or not cfg["from_address"]:
        return {"ok": False, "outcome": "failed", "error": "SMTP customer email is not configured"}
    if "@" not in to or len(to) > 320:
        return {"ok": False, "outcome": "failed", "error": "invalid email recipient"}
    if not message.strip():
        return {"ok": False, "outcome": "failed", "error": "email message is empty"}
    msg = EmailMessage()
    msg["From"] = cfg["from_address"]
    msg["To"] = to
    msg["Subject"] = subject[:998] or "Message from our team"
    if html:
        msg.set_content(message.replace("<br>", "\n"))
        msg.add_alternative(message, subtype="html")
    else:
        msg.set_content(message)
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as server:
            if cfg["use_tls"]:
                server.starttls()
            server.login(cfg["username"], cfg["password"])
            server.send_message(msg)
        return {"ok": True, "outcome": "succeeded", "recipient": to, "channel": "email"}
    except (smtplib.SMTPException, OSError) as exc:
        # SMTP may accept the message and fail while the connection is closing;
        # surface that as uncertain so the execution reconciler can decide retry.
        return {"ok": False, "outcome": "uncertain", "error": str(exc)[:500], "recipient": to, "channel": "email"}


async def send_customer_message(*, channel: str, recipient: str, message: str,
                                subject: str = "", html: bool = False,
                                social_account_id: str = "") -> dict[str, Any]:
    """Send through one of the bounded customer channels."""
    channel = str(channel or "").strip().lower()
    recipient = str(recipient or "").strip()
    message = str(message or "").strip()
    if channel not in SUPPORTED_CHANNELS:
        return {"ok": False, "outcome": "failed", "error": f"unsupported customer communication channel: {channel}"}
    if not recipient or not message:
        return {"ok": False, "outcome": "failed", "error": "recipient and message are required"}

    if channel == "email":
        result = await asyncio.to_thread(_send_email_sync, recipient, subject, message, html)
        return result

    if not social_account_id:
        return {"ok": False, "outcome": "failed", "error": "WhatsApp requires social_account_id"}
    from repositories.social_account_repository import SocialAccountRepository
    from services.connectors.whatsapp_connector import WhatsAppConnector
    repo = SocialAccountRepository()
    account = repo.find_by_id(social_account_id)
    if account is None or not account.enabled:
        return {"ok": False, "outcome": "failed", "error": "WhatsApp account not found or disabled"}
    if str(account.platform).lower() != "whatsapp":
        return {"ok": False, "outcome": "failed", "error": "selected account is not WhatsApp"}
    result = await asyncio.to_thread(WhatsAppConnector(account).publish, message, to_number=recipient)
    if not getattr(result, "ok", False):
        return {"ok": False, "outcome": getattr(result, "outcome", "failed") or "failed",
                "error": getattr(result, "error", None) or "WhatsApp send failed", "recipient": recipient, "channel": "whatsapp"}
    return {"ok": True, "outcome": "succeeded", "provider_message_id": getattr(result, "post_id", ""),
            "recipient": recipient, "channel": "whatsapp"}
