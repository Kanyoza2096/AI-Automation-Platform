# ═══════════════════════════════════════════════════════════════════════════
# NOTIFICATION SERVICE — v2.0
# Telegram-based notification delivery.
# Integrates with Condition Engine for autonomous alerting.
#
# Provider: Telegram Bot API (free, instant, always works)
# ═══════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.services.email")


# ═══════════════════════════════════════════════════════════════════════════
# NOTIFICATION SERVICE
# ═══════════════════════════════════════════════════════════════════════════

class EmailService:
    """
    Telegram-based notification delivery service.

    Provider:
        - Telegram Bot API (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)

    Usage:
        notify = EmailService()
        notify.send_telegram("🚨 System alert: something happened")
        notify.alert("Title", "Message")
    """

    def __init__(self):
        # Telegram config
        self.telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.health_alert_chat_id = os.environ.get("HEALTH_ALERT_CHAT_ID", "")

        # Admin recipients (kept for compatibility, Telegram is primary)
        self.admin_emails = [
            e.strip()
            for e in os.environ.get("ADMIN_EMAILS", "").split(",")
            if e.strip()
        ]

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════════

    def send(
        self,
        to: str = "",
        subject: str = "",
        body: str = "",
        template: str = "",
        context: Dict[str, Any] = None,
        html: bool = True,
    ) -> bool:
        """Send a notification via Telegram. 'to', 'subject', 'body' kept for API compatibility."""
        message = f"<b>{subject}</b>\n\n{body}" if subject and body else (subject or body or "Kanyoza Notification")
        return self.send_telegram(message)

    def send_to_admins(self, subject: str, body: str = "", **kwargs) -> bool:
        """Send to admin via Telegram."""
        message = f"<b>📧 {subject}</b>"
        if body:
            message += f"\n\n{body[:1000]}"
        return self.send_telegram(message)

    def send_telegram(self, message: str, chat_id: str = "") -> bool:
        """Send a Telegram message. Supports <b>, <i>, <u>, <s>, <code>, <pre>, <a> HTML tags."""
        return self._send_telegram(message, chat_id)

    def alert(self, title: str, message: str) -> bool:
        """Send alert via Telegram."""
        return self.send_telegram(f"<b>🚨 {title}</b>\n\n{message}")

    # ═══════════════════════════════════════════════════════════════════
    # PROVIDER
    # ═══════════════════════════════════════════════════════════════════

    def _send_telegram(self, message: str, chat_id: str = "") -> bool:
        """Send message via Telegram Bot API."""
        try:
            import requests

            token = self.telegram_bot_token
            target_chat = chat_id or self.telegram_chat_id or self.health_alert_chat_id

            if not token or not target_chat:
                logger.debug("Telegram not configured")
                return False

            # Strip HTML tags Telegram doesn't support
            clean = re.sub(
                r'</?(div|p|h[1-6]|span|ol|ul|li|table|tr|td|th|hr|br|strong|em|blockquote)\b[^>]*>',
                '',
                message,
            )
            clean = clean.replace('<br>', '\n').replace('<br/>', '\n').replace('<hr>', '\n――――――\n')
            clean = clean.replace('</p>', '\n').replace('<p>', '')

            url = f"https://api.telegram.org/bot{token}/sendMessage"
            response = requests.post(
                url,
                json={
                    "chat_id": target_chat,
                    "text": clean[:4000],
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    logger.info("Telegram message sent to %s", target_chat)
                    return True
                else:
                    logger.warning("Telegram HTML parse failed, retrying as plain text")
                    return self._send_telegram_plain(clean[:4000], target_chat, token)
            else:
                logger.error("Telegram returned %d: %s", response.status_code, response.text[:200])

        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)

        return False

    def _send_telegram_plain(self, message: str, chat_id: str, token: str) -> bool:
        """Fallback: send plain text without HTML parsing."""
        try:
            import requests
            clean = re.sub(r'<[^>]+>', '', message)

            url = f"https://api.telegram.org/bot{token}/sendMessage"
            response = requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": clean[:4000],
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )

            if response.status_code == 200 and response.json().get("ok"):
                logger.info("Telegram plain text sent")
                return True

        except Exception as exc:
            logger.error("Telegram plain text failed: %s", exc)

        return False


# ═══════════════════════════════════════════════════════════════════════════
# PREDEFINED NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════

class KanyozaNotifications:
    """Pre-built notifications for common events."""

    def __init__(self, email_service: EmailService):
        self.email = email_service

    def alert(self, title: str, message: str):
        self.email.alert(title, message)

    def director_alert(self, payload: dict):
        alert_type = payload.get("type", "Unknown")
        task = payload.get("task", {})
        description = task.get("description", "No description") if isinstance(task, dict) else str(task)
        reason = task.get("reason", "") if isinstance(task, dict) else ""

        telegram_msg = f"<b>👔 Director: {alert_type}</b>\n{description}"
        if reason:
            telegram_msg += f"\n\n<i>{reason}</i>"

        self.email.send_telegram(telegram_msg)


# ═══════════════════════════════════════════════════════════════════════════
# CONDITION ENGINE INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

def register_email_actions(condition_engine, email_service: EmailService):
    """Register notification actions in the Condition Engine."""
    notifications = KanyozaNotifications(email_service)

    def send_email(value, params, context):
        subject = params.get("subject") or str(value)
        body = params.get("body", "")
        message = f"<b>{subject}</b>"
        if body:
            message += f"\n\n{body[:500]}"
        email_service.send_telegram(message)

    def notify_admins_email(value, params, context):
        subject = params.get("subject") or str(value)
        email_service.send_telegram(f"<b>📧 {subject}</b>")

    if hasattr(condition_engine, '_executor'):
        condition_engine._executor.register("send_email", send_email)
        condition_engine._executor.register("notify_admins_email", notify_admins_email)
        logger.info("Notification actions registered with Condition Engine")


# ═══════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

email_service = EmailService()
kanyoza_notifications = KanyozaNotifications(email_service)
