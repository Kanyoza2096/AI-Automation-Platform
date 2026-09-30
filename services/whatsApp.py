"""
WhatsApp Business Integration Service v1.0
- Send notifications via WhatsApp
- WhatsApp Cloud API (Meta)
- Template messages support
"""

import logging
from typing import Optional

from config.settings import settings
from utils.http_client import HttpClient

logger = logging.getLogger("kanyoza.services.whatsapp")

_http = HttpClient("whatsapp", default_timeout=15)


class WhatsAppService:
    """WhatsApp Business API integration."""

    def __init__(self):
        self.token = getattr(settings, 'whatsapp_token', '')
        self.phone_id = getattr(settings, 'whatsapp_phone_id', '')
        self.api_version = "v22.0"
        self.base_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_id}"

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.phone_id)

    def send_message(self, to_number: str, message: str) -> bool:
        """Send a text message via WhatsApp."""
        if not self.is_configured:
            return False

        try:
            response = _http.post(
                f"{self.base_url}/messages",
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
                json={
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": to_number,
                    "type": "text",
                    "text": {"preview_url": False, "body": message[:4096]},
                },
                timeout=15,
            )
            if response.status_code == 200:
                logger.info(f"✅ WhatsApp message sent to {to_number}")
                return True
            else:
                logger.error(f"WhatsApp send failed: {response.status_code} {response.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"WhatsApp error: {e}")
            return False

    def send_daily_summary(self, to_number: str, stats: dict) -> bool:
        """Send daily bot summary via WhatsApp."""
        overview = stats.get('overview', {})
        message = (
            f"📊 *Kanyoza Daily Summary*\n\n"
            f"💬 Messages: {overview.get('messages_received', 0)} in / {overview.get('messages_sent', 0)} out\n"
            f"📝 Posts: {overview.get('posts_published', 0)} published\n"
            f"👥 Active users: {overview.get('active_users_24h', 0)}\n"
            f"⚠️ Errors: {overview.get('total_errors', 0)}\n"
            f"🛡️ Guardian: {overview.get('guardian_critical', 0)} critical issues"
        )
        return self.send_message(to_number, message)

    def send_alert(self, to_number: str, alert_message: str) -> bool:
        """Send an urgent alert via WhatsApp."""
        message = f"🚨 *Alert*\n\n{alert_message}"
        return self.send_message(to_number, message)


whatsapp = WhatsAppService()
