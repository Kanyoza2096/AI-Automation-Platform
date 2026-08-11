"""
KANYOZA NOTIFICATION SERVICE v3.0 — "THE ANNOUNCER"

Multi-channel notification delivery with rate limiting, batching,
retry, and deduplication.

Primary: Telegram Bot API (free, instant, always works)
Future: Email (SMTP), Slack, Discord webhooks

v3.0 UPGRADES:
  - Rate limiting (max messages per minute per channel)
  - Message batching (queue rapid alerts into digest)
  - Retry with exponential backoff (3 attempts)
  - Message deduplication (skip duplicate alerts within 60s)
  - Alert severity routing (critical → instant, info → batched)
  - Health check & statistics
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.services.email")


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL SERVICE (Telegram-backed notification delivery)
# ═══════════════════════════════════════════════════════════════════════════

class EmailService:
    """
    Telegram-based notification delivery service.

    Provider:
        - Telegram Bot API (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)

    Features:
        - Rate limiting (max 20 messages/min)
        - Message batching (non-critical alerts batched into digest)
        - Retry with exponential backoff (3 attempts)
        - Deduplication (identical messages within 60s skipped)

    Usage:
        notify = EmailService()
        notify.send_telegram("🚨 System alert: something happened")
        notify.alert("Title", "Message")
    """

    # Rate limiting
    MAX_MESSAGES_PER_MINUTE = 20
    BATCH_WINDOW_SECONDS = 10
    DEDUP_WINDOW_SECONDS = 60
    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 1.0

    def __init__(self):
        # ── Telegram config ─────────────────────────────────────────
        self.telegram_bot_token = os.environ.get(
            "TELEGRAM_BOT_TOKEN", ""
        )
        self.telegram_chat_id = os.environ.get(
            "TELEGRAM_CHAT_ID", ""
        )
        self.health_alert_chat_id = os.environ.get(
            "HEALTH_ALERT_CHAT_ID", ""
        )

        # ── Admin recipients (kept for future email support) ────────
        self.admin_emails = [
            e.strip()
            for e in os.environ.get(
                "ADMIN_EMAILS", ""
            ).split(",")
            if e.strip()
        ]

        # ── Rate limiting state ─────────────────────────────────────
        self._message_timestamps: deque = deque()
        self._lock = threading.Lock()

        # ── Batching state ──────────────────────────────────────────
        self._batch: List[str] = []
        self._batch_lock = threading.Lock()
        self._batch_timer: Optional[threading.Timer] = None

        # ── Deduplication state ─────────────────────────────────────
        self._recent_messages: Dict[str, float] = {}

        # ── Statistics ──────────────────────────────────────────────
        self._sent_count = 0
        self._failed_count = 0
        self._rate_limited_count = 0
        self._deduped_count = 0

        logger.info(
            "EmailService v3.0 initialized (Telegram: %s)",
            "configured" if self.telegram_bot_token else "disabled",
        )

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
        priority: str = "normal",
    ) -> bool:
        """Send a notification via Telegram.

        'to', 'subject', 'body', 'template', 'context', 'html'
        kept for backward API compatibility.
        """
        message = (
            f"<b>{subject}</b>\n\n{body}"
            if subject and body
            else (subject or body or "Kanyoza Notification")
        )
        return self.send_telegram(message, priority=priority)

    def send_to_admins(
        self, subject: str, body: str = "", **kwargs
    ) -> bool:
        """Send to admin via Telegram."""
        message = f"<b>📧 {subject}</b>"
        if body:
            message += f"\n\n{body[:1000]}"
        return self.send_telegram(message)

    def send_telegram(
        self,
        message: str,
        chat_id: str = "",
        priority: str = "normal",
    ) -> bool:
        """Send a Telegram message with rate limiting and dedup.

        Supports <b>, <i>, <u>, <s>, <code>, <pre>, <a> HTML tags.

        Args:
            message: HTML-formatted message
            chat_id: Override chat ID
            priority: 'critical' (bypasses rate limit) or 'normal'

        Returns:
            True if sent/queued, False if completely failed
        """
        # ── Deduplication ────────────────────────────────────────
        msg_hash = hashlib.md5(
            message.encode()
        ).hexdigest()
        now = time.monotonic()

        with self._lock:
            if msg_hash in self._recent_messages:
                last_sent = self._recent_messages[msg_hash]
                if (
                    now - last_sent
                    < self.DEDUP_WINDOW_SECONDS
                ):
                    self._deduped_count += 1
                    logger.debug(
                        "Duplicate message suppressed: %s",
                        message[:80],
                    )
                    return True
            self._recent_messages[msg_hash] = now

        # Clean old entries periodically
        if len(self._recent_messages) > 500:
            cutoff = (
                now - self.DEDUP_WINDOW_SECONDS * 2
            )
            self._recent_messages = {
                k: v
                for k, v in self._recent_messages.items()
                if v > cutoff
            }

        # ── Rate limiting ────────────────────────────────────────
        if not self._check_rate_limit():
            if priority == "critical":
                logger.warning(
                    "Rate limit bypassed for critical alert"
                )
            else:
                self._batch_message(message, chat_id)
                self._rate_limited_count += 1
                return True

        # ── Send ──────────────────────────────────────────────────
        return self._send_telegram_with_retry(
            message, chat_id
        )

    def alert(
        self,
        title: str,
        message: str,
        priority: str = "critical",
    ) -> bool:
        """Send a critical alert via Telegram."""
        return self.send_telegram(
            f"<b>🚨 {title}</b>\n\n{message}",
            priority=priority,
        )

    # ═══════════════════════════════════════════════════════════════════
    # RATE LIMITING
    # ═══════════════════════════════════════════════════════════════════

    def _check_rate_limit(self) -> bool:
        """Check if we're under the rate limit."""
        now = time.monotonic()
        cutoff = now - 60

        with self._lock:
            while (
                self._message_timestamps
                and self._message_timestamps[0] < cutoff
            ):
                self._message_timestamps.popleft()

            if (
                len(self._message_timestamps)
                >= self.MAX_MESSAGES_PER_MINUTE
            ):
                return False

            self._message_timestamps.append(now)
            return True

    # ═══════════════════════════════════════════════════════════════════
    # MESSAGE BATCHING
    # ═══════════════════════════════════════════════════════════════════

    def _batch_message(
        self, message: str, chat_id: str
    ) -> None:
        """Queue a message for batched delivery."""
        with self._batch_lock:
            self._batch.append(message)

            if self._batch_timer is None:
                self._batch_timer = threading.Timer(
                    self.BATCH_WINDOW_SECONDS,
                    self._flush_batch,
                )
                self._batch_timer.daemon = True
                self._batch_timer.start()

    def _flush_batch(self) -> None:
        """Send all queued messages as a digest."""
        with self._batch_lock:
            if not self._batch:
                self._batch_timer = None
                return

            messages = list(self._batch)
            self._batch.clear()
            self._batch_timer = None

        count = len(messages)
        if count == 1:
            digest = messages[0]
        else:
            digest = (
                f"<b>📊 Digest: {count} notifications</b>\n"
                f"<i>Batched over "
                f"{self.BATCH_WINDOW_SECONDS}s</i>\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                + "\n――――――\n".join(messages)
            )

        self._send_telegram_with_retry(digest)
        logger.info(
            "EmailService: flushed batch of %d messages",
            count,
        )

    # ═══════════════════════════════════════════════════════════════════
    # TELEGRAM PROVIDER
    # ═══════════════════════════════════════════════════════════════════

    def _send_telegram_with_retry(
        self, message: str, chat_id: str = ""
    ) -> bool:
        """Send with exponential backoff retry."""
        for attempt in range(self.MAX_RETRIES):
            if self._send_telegram(message, chat_id):
                self._sent_count += 1
                return True

            if attempt < self.MAX_RETRIES - 1:
                delay = (
                    self.RETRY_BASE_DELAY
                    * (2 ** attempt)
                )
                logger.warning(
                    "Telegram send failed (attempt %d/%d), "
                    "retrying in %.1fs",
                    attempt + 1,
                    self.MAX_RETRIES,
                    delay,
                )
                time.sleep(delay)

        self._failed_count += 1
        return False

    def _send_telegram(
        self, message: str, chat_id: str = ""
    ) -> bool:
        """Send message via Telegram Bot API."""
        try:
            import requests

            token = self.telegram_bot_token
            target_chat = (
                chat_id
                or self.telegram_chat_id
                or self.health_alert_chat_id
            )

            if not token or not target_chat:
                logger.debug("Telegram not configured")
                return False

            # Strip HTML tags Telegram doesn't support
            clean = re.sub(
                r'</?(div|p|h[1-6]|span|ol|ul|li|table|tr|'
                r'td|th|hr|br|strong|em|blockquote)\b[^>]*>',
                '',
                message,
            )
            clean = (
                clean.replace('<br>', '\n')
                .replace('<br/>', '\n')
                .replace('<hr>', '\n――――――\n')
                .replace('</p>', '\n')
                .replace('<p>', '')
            )

            url = (
                f"https://api.telegram.org/bot{token}"
                f"/sendMessage"
            )
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
                    logger.info(
                        "Telegram message sent to %s",
                        target_chat,
                    )
                    return True
                else:
                    logger.warning(
                        "Telegram HTML parse failed, "
                        "retrying as plain text"
                    )
                    return self._send_telegram_plain(
                        clean[:4000],
                        target_chat,
                        token,
                    )
            else:
                logger.error(
                    "Telegram returned %d: %s",
                    response.status_code,
                    response.text[:200],
                )

        except Exception as exc:
            logger.error(
                "Telegram send failed: %s", exc
            )

        return False

    def _send_telegram_plain(
        self,
        message: str,
        chat_id: str,
        token: str,
    ) -> bool:
        """Fallback: send plain text without HTML parsing."""
        try:
            import requests

            clean = re.sub(r'<[^>]+>', '', message)

            url = (
                f"https://api.telegram.org/bot{token}"
                f"/sendMessage"
            )
            response = requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": clean[:4000],
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )

            if (
                response.status_code == 200
                and response.json().get("ok")
            ):
                logger.info(
                    "Telegram plain text sent"
                )
                return True

        except Exception as exc:
            logger.error(
                "Telegram plain text failed: %s", exc
            )

        return False

    # ═══════════════════════════════════════════════════════════════════
    # HEALTH & STATISTICS
    # ═══════════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        """Return notification statistics."""
        with self._lock:
            rate_count = len(self._message_timestamps)
        return {
            "sent": self._sent_count,
            "failed": self._failed_count,
            "rate_limited": self._rate_limited_count,
            "deduped": self._deduped_count,
            "messages_last_minute": rate_count,
            "rate_limit": self.MAX_MESSAGES_PER_MINUTE,
            "telegram_configured": bool(
                self.telegram_bot_token
            ),
        }

    def health_check(self) -> dict:
        """Quick health check for the notification service."""
        stats = self.get_stats()
        status = "healthy"

        if not stats["telegram_configured"]:
            status = "disabled"
        elif (
            stats["failed"]
            > stats["sent"] * 0.1
            and stats["sent"] > 10
        ):
            status = "degraded"

        return {
            "status": status,
            "stats": stats,
        }


# ═══════════════════════════════════════════════════════════════════════════
# PREDEFINED NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════

class KanyozaNotifications:
    """Pre-built notifications for common events."""

    def __init__(self, email_service: EmailService):
        self.email = email_service

    def alert(
        self, title: str, message: str
    ) -> bool:
        return self.email.alert(title, message)

    def director_alert(
        self, payload: dict
    ) -> bool:
        alert_type = payload.get("type", "Unknown")
        task = payload.get("task", {})
        description = (
            task.get("description", "No description")
            if isinstance(task, dict)
            else str(task)
        )
        reason = (
            task.get("reason", "")
            if isinstance(task, dict)
            else ""
        )

        telegram_msg = (
            f"<b>👔 Director: {alert_type}</b>\n"
            f"{description}"
        )
        if reason:
            telegram_msg += f"\n\n<i>{reason}</i>"

        return self.email.send_telegram(telegram_msg)


# ═══════════════════════════════════════════════════════════════════════════
# CONDITION ENGINE INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

def register_email_actions(
    condition_engine, email_service: EmailService
) -> None:
    """Register notification actions with the Condition Engine.

    Wires the email service into the condition engine's notification
    handler so autonomous decisions trigger real alerts.
    """
    notifications = KanyozaNotifications(email_service)

    # Store on the condition engine for access by action executors
    if hasattr(condition_engine, '__dict__'):
        condition_engine._notification_handler = notifications
        logger.info(
            "Notification actions registered with Condition Engine"
        )


# ═══════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

email_service = EmailService()
kanyoza_notifications = KanyozaNotifications(email_service)
