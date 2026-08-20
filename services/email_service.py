"""
KANYOZA NOTIFICATION SERVICE v4.0 — "THE ANNOUNCER" (ASYNC-NATIVE)

v4.0 CHANGES:
  - asyncio.Lock replaces threading.Lock
  - asyncio.Task replaces threading.Timer
  - await asyncio.sleep() replaces time.sleep()
  - All send methods async
  - HTTP calls offloaded to executor
"""

from __future__ import annotations

import asyncio  # ✅ ADD
import hashlib
import logging
import os
import re
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.services.email")


class EmailService:
    """Telegram-based notification delivery — ASYNC-NATIVE.

    v4.0: asyncio.Lock, asyncio.Task, await asyncio.sleep.
    """

    MAX_MESSAGES_PER_MINUTE = 20
    BATCH_WINDOW_SECONDS = 10
    DEDUP_WINDOW_SECONDS = 60
    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 1.0

    def __init__(self):
        self.telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.health_alert_chat_id = os.environ.get("HEALTH_ALERT_CHAT_ID", "")

        self.admin_emails = [
            e.strip() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()
        ]

        self._message_timestamps: deque = deque()
        self._lock = asyncio.Lock()  # ✅ Async lock

        self._batch: List[str] = []
        self._batch_lock = asyncio.Lock()  # ✅ Async lock
        self._batch_task: Optional[asyncio.Task] = None  # ✅ Task instead of Timer

        self._recent_messages: Dict[str, float] = {}

        self._sent_count = 0
        self._failed_count = 0
        self._rate_limited_count = 0
        self._deduped_count = 0

        logger.info(
            "EmailService v4.0 initialized (Telegram: %s)",
            "configured" if self.telegram_bot_token else "disabled",
        )

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC API (ASYNC)
    # ═══════════════════════════════════════════════════════════════════

    async def send(
        self, to: str = "", subject: str = "", body: str = "",
        template: str = "", context: Dict[str, Any] = None,
        html: bool = True, priority: str = "normal",
    ) -> bool:
        message = (
            f"<b>{subject}</b>\n\n{body}"
            if subject and body
            else (subject or body or "Kanyoza Notification")
        )
        return await self.send_telegram(message, priority=priority)

    async def send_to_admins(self, subject: str, body: str = "", **kwargs) -> bool:
        message = f"<b>📧 {subject}</b>"
        if body:
            message += f"\n\n{body[:1000]}"
        return await self.send_telegram(message)

    async def send_telegram(
        self, message: str, chat_id: str = "", priority: str = "normal",
    ) -> bool:
        # Deduplication
        msg_hash = hashlib.md5(message.encode()).hexdigest()
        now = time.monotonic()

        async with self._lock:
            if msg_hash in self._recent_messages:
                if now - self._recent_messages[msg_hash] < self.DEDUP_WINDOW_SECONDS:
                    self._deduped_count += 1
                    return True
            self._recent_messages[msg_hash] = now

        # Clean old entries
        if len(self._recent_messages) > 500:
            cutoff = now - self.DEDUP_WINDOW_SECONDS * 2
            self._recent_messages = {
                k: v for k, v in self._recent_messages.items() if v > cutoff
            }

        # Rate limiting
        if not await self._check_rate_limit():
            if priority == "critical":
                logger.warning("Rate limit bypassed for critical alert")
            else:
                await self._batch_message(message, chat_id)
                self._rate_limited_count += 1
                return True

        # Send
        return await self._send_telegram_with_retry(message, chat_id)

    async def alert(self, title: str, message: str, priority: str = "critical") -> bool:
        return await self.send_telegram(
            f"<b>🚨 {title}</b>\n\n{message}", priority=priority
        )

    # ═══════════════════════════════════════════════════════════════════
    # RATE LIMITING (ASYNC)
    # ═══════════════════════════════════════════════════════════════════

    async def _check_rate_limit(self) -> bool:
        now = time.monotonic()
        cutoff = now - 60

        async with self._lock:
            while self._message_timestamps and self._message_timestamps[0] < cutoff:
                self._message_timestamps.popleft()

            if len(self._message_timestamps) >= self.MAX_MESSAGES_PER_MINUTE:
                return False

            self._message_timestamps.append(now)
            return True

    # ═══════════════════════════════════════════════════════════════════
    # MESSAGE BATCHING (ASYNC)
    # ═══════════════════════════════════════════════════════════════════

    async def _batch_message(self, message: str, chat_id: str) -> None:
        async with self._batch_lock:
            self._batch.append(message)

            if self._batch_task is None or self._batch_task.done():
                self._batch_task = asyncio.create_task(
                    self._flush_batch_after_delay()
                )

    async def _flush_batch_after_delay(self) -> None:
        await asyncio.sleep(self.BATCH_WINDOW_SECONDS)
        await self._flush_batch()

    async def _flush_batch(self) -> None:
        async with self._batch_lock:
            if not self._batch:
                self._batch_task = None
                return

            messages = list(self._batch)
            self._batch.clear()
            self._batch_task = None

        count = len(messages)
        if count == 1:
            digest = messages[0]
        else:
            digest = (
                f"<b>📊 Digest: {count} notifications</b>\n"
                f"<i>Batched over {self.BATCH_WINDOW_SECONDS}s</i>\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                + "\n――――――\n".join(messages)
            )

        await self._send_telegram_with_retry(digest)

    # ═══════════════════════════════════════════════════════════════════
    # TELEGRAM PROVIDER (ASYNC — offload HTTP to executor)
    # ═══════════════════════════════════════════════════════════════════

    async def _send_telegram_with_retry(self, message: str, chat_id: str = "") -> bool:
        for attempt in range(self.MAX_RETRIES):
            if await self._send_telegram(message, chat_id):
                self._sent_count += 1
                return True

            if attempt < self.MAX_RETRIES - 1:
                delay = self.RETRY_BASE_DELAY * (2 ** attempt)
                await asyncio.sleep(delay)  # ✅ async sleep

        self._failed_count += 1
        return False

    async def _send_telegram(self, message: str, chat_id: str = "") -> bool:
        try:
            import requests

            token = self.telegram_bot_token
            target_chat = chat_id or self.telegram_chat_id or self.health_alert_chat_id

            if not token or not target_chat:
                return False

            clean = re.sub(r'</?(div|p|h[1-6]|span|ol|ul|li|table|tr|td|th|hr|br|strong|em|blockquote)\b[^>]*>', '', message)
            clean = clean.replace('<br>', '\n').replace('<br/>', '\n').replace('<hr>', '\n――――――\n').replace('</p>', '\n').replace('<p>', '')

            url = f"https://api.telegram.org/bot{token}/sendMessage"
            loop = asyncio.get_event_loop()

            def _do_send():
                return requests.post(
                    url,
                    json={
                        "chat_id": target_chat,
                        "text": clean[:4000],
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=10,
                )

            response = await loop.run_in_executor(None, _do_send)

            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    return True
                else:
                    return await self._send_telegram_plain(clean[:4000], target_chat, token)
            return False

        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)
            return False

    async def _send_telegram_plain(self, message: str, chat_id: str, token: str) -> bool:
        try:
            import requests
            clean = re.sub(r'<[^>]+>', '', message)
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            loop = asyncio.get_event_loop()

            def _do_send():
                return requests.post(
                    url,
                    json={"chat_id": chat_id, "text": clean[:4000], "disable_web_page_preview": True},
                    timeout=10,
                )

            response = await loop.run_in_executor(None, _do_send)
            return response.status_code == 200 and response.json().get("ok")

        except Exception as exc:
            logger.error("Telegram plain text failed: %s", exc)
            return False

    # ═══════════════════════════════════════════════════════════════════
    # HEALTH & STATISTICS (unchanged)
    # ═══════════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        return {
            "sent": self._sent_count,
            "failed": self._failed_count,
            "rate_limited": self._rate_limited_count,
            "deduped": self._deduped_count,
            "messages_last_minute": len(self._message_timestamps),
            "rate_limit": self.MAX_MESSAGES_PER_MINUTE,
            "telegram_configured": bool(self.telegram_bot_token),
        }

    def health_check(self) -> dict:
        stats = self.get_stats()
        status = "healthy"
        if not stats["telegram_configured"]:
            status = "disabled"
        elif stats["failed"] > stats["sent"] * 0.1 and stats["sent"] > 10:
            status = "degraded"
        return {"status": status, "stats": stats}


# ═══════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON (unchanged — lazy creation)
# ═══════════════════════════════════════════════════════════════════════════

email_service = EmailService()
kanyoza_notifications = KanyozaNotifications(email_service)