"""
Notifier Service v1.0
- Slack webhook notifications
- Discord webhook notifications
- Email via SendGrid
- Multi-channel alerting
"""

import logging
import requests
from datetime import datetime
from typing import Optional

from config.settings import settings

logger = logging.getLogger("kanyoza.services.notifier")


class NotifierService:
    """Multi-channel notification service."""

    def __init__(self):
        self.slack_webhook = None  # Set via settings or DB
        self.discord_webhook = None
        self.email_recipient = settings.owner_psid  # Default to owner

    # ------------------------------------------------------------------
    # SLACK
    # ------------------------------------------------------------------
    def send_slack(self, message: str, color: str = "#4F46E5") -> bool:
        """Send a Slack message via webhook."""
        if not self.slack_webhook:
            return False

        try:
            payload = {
                "attachments": [{
                    "color": color,
                    "text": message,
                    "footer": f"Kanyoza Systems • {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}",
                }]
            }
            response = requests.post(self.slack_webhook, json=payload, timeout=10)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Slack notification failed: {e}")
            return False

    # ------------------------------------------------------------------
    # DISCORD
    # ------------------------------------------------------------------
    def send_discord(self, message: str, title: str = "Kanyoza Alert") -> bool:
        """Send a Discord message via webhook."""
        if not self.discord_webhook:
            return False

        try:
            payload = {
                "embeds": [{
                    "title": title,
                    "description": message,
                    "color": 5204493,
                    "timestamp": datetime.now().isoformat(),
                }]
            }
            response = requests.post(self.discord_webhook, json=payload, timeout=10)
            return response.status_code == 204
        except Exception as e:
            logger.error(f"Discord notification failed: {e}")
            return False

    # ------------------------------------------------------------------
    # EMAIL (SendGrid)
    # ------------------------------------------------------------------
    def send_email(self, subject: str, body: str) -> bool:
        """Send email via SendGrid API."""
        sendgrid_key = getattr(settings, 'sendgrid_key', None)
        if not sendgrid_key or not self.email_recipient:
            return False

        try:
            response = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {sendgrid_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "personalizations": [{"to": [{"email": self.email_recipient}]}],
                    "from": {"email": "noreply@kanyoza.systems", "name": "Kanyoza Systems"},
                    "subject": subject,
                    "content": [{"type": "text/plain", "value": body}],
                },
                timeout=15,
            )
            return response.status_code == 202
        except Exception as e:
            logger.error(f"Email notification failed: {e}")
            return False

    # ------------------------------------------------------------------
    # SMART ALERTS
    # ------------------------------------------------------------------
    def alert(self, message: str, level: str = "info", channels: list = None):
        """
        Send alert to configured channels.
        Levels: info, warning, error, critical
        """
        channels = channels or ['slack']

        color_map = {
            'info': '#3B82F6',
            'warning': '#F59E0B',
            'error': '#EF4444',
            'critical': '#7C3AED',
        }
        color = color_map.get(level, '#4F46E5')

        emoji_map = {
            'info': 'ℹ️',
            'warning': '⚠️',
            'error': '🚨',
            'critical': '🔴',
        }
        emoji = emoji_map.get(level, '📢')

        formatted = f"{emoji} [{level.upper()}] {message}"

        results = {}
        if 'slack' in channels:
            results['slack'] = self.send_slack(formatted, color)
        if 'discord' in channels:
            results['discord'] = self.send_discord(formatted, f"Kanyoza {level.upper()}")
        if 'email' in channels:
            results['email'] = self.send_email(f"Kanyoza {level.upper()}: {message[:50]}", message)

        return results

    # ------------------------------------------------------------------
    # PREDEFINED ALERTS
    # ------------------------------------------------------------------
    def alert_bot_offline(self):
        self.alert("Bot is offline or unresponsive", "critical", ['slack', 'discord', 'email'])

    def alert_guardian_critical(self, count: int):
        self.alert(f"Code Guardian found {count} critical issues", "critical", ['slack', 'discord'])

    def alert_rate_limit_blocked(self, user: str):
        self.alert(f"User {user} blocked for rate limit abuse", "warning", ['slack'])

    def alert_post_failed(self, topic: str, reason: str):
        self.alert(f"Post failed: {topic[:80]} — {reason}", "error", ['slack'])

    def alert_new_tenant(self, name: str, plan: str):
        self.alert(f"🎉 New tenant: {name} ({plan} plan)", "info", ['slack', 'discord'])

    def daily_summary(self, stats: dict):
        overview = stats.get('overview', {})
        message = (
            f"📊 Daily Summary\n"
            f"💬 Messages: {overview.get('messages_received', 0)} in / {overview.get('messages_sent', 0)} out\n"
            f"📝 Posts: {overview.get('posts_published', 0)} published\n"
            f"👥 Active users: {overview.get('active_users_24h', 0)}\n"
            f"⚠️ Errors: {overview.get('total_errors', 0)}\n"
            f"🛡️ Guardian: {overview.get('guardian_critical', 0)} critical issues"
        )
        self.alert(message, "info", ['slack'])


notifier = NotifierService()
