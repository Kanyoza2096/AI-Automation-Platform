# ═══════════════════════════════════════════════════════════════════════════
# EMAIL SERVICE — v1.2
# Transactional and notification email delivery.
# Integrates with Condition Engine for autonomous alerting.
#
# Providers: Resend API → Gmail SMTP → Telegram (all with fallback)
# Templates: Jinja2 (already in your stack)
# ═══════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger("kanyoza.services.email")


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL SERVICE
# ═══════════════════════════════════════════════════════════════════════════

class EmailService:
    """
    Multi-provider notification delivery service.

    Providers (auto-selected in order):
        - Resend API (primary email)
        - Gmail SMTP (fallback email)
        - Telegram Bot (instant alerts, free)

    Usage:
        email = EmailService()
        email.send(to="admin@org.com", subject="Alert", body="Something happened")
        email.send_telegram("🚨 System alert: something happened")
    """

    def __init__(self):
        # SMTP config
        self.smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        self.smtp_username = os.environ.get("SMTP_USERNAME", "")
        self.smtp_password = os.environ.get("SMTP_PASSWORD", "")

        # Resend config
        self.resend_api_key = os.environ.get("RESEND_API_KEY", "")

        # Telegram config
        self.telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.health_alert_chat_id = os.environ.get("HEALTH_ALERT_CHAT_ID", "")

        # Default sender
        self.default_from = os.environ.get(
            "EMAIL_FROM",
            self.smtp_username or "kanyoza@yourorg.org",
        )

        # Admin recipients
        self.admin_emails = [
            e.strip()
            for e in os.environ.get("ADMIN_EMAILS", "").split(",")
            if e.strip()
        ]

        # Jinja2 templates
        self._jinja: Optional[Environment] = None
        self._init_templates()

    def _init_templates(self):
        """Initialize Jinja2 for email templates."""
        templates_dir = Path(__file__).parent.parent / "templates" / "emails"
        if templates_dir.exists():
            self._jinja = Environment(
                loader=FileSystemLoader(str(templates_dir)),
            )
            logger.info("Email templates loaded from %s", templates_dir)
        else:
            logger.debug("No email templates directory at %s", templates_dir)

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════════

    def send(
        self,
        to: str,
        subject: str,
        body: str = "",
        template: str = "",
        context: Dict[str, Any] = None,
        html: bool = True,
    ) -> bool:
        """
        Send an email.

        Args:
            to: Recipient email address
            subject: Email subject line
            body: Plain text or HTML body (ignored if template is set)
            template: Jinja2 template name (e.g., "weekly_report")
            context: Template variables
            html: Whether body contains HTML

        Returns:
            True if sent successfully
        """
        if template and self._jinja:
            try:
                tmpl = self._jinja.get_template(f"{template}.html")
                body = tmpl.render(**(context or {}))
                html = True
            except Exception as exc:
                logger.error("Email template '%s' failed: %s", template, exc)
                if not body:
                    body = f"Kanyoza Notification\n\nTemplate '{template}' failed to render."

        if not body:
            body = subject

        # Try Resend API first
        if self.resend_api_key:
            if self._send_resend(to, subject, body, html):
                return True
            logger.warning("Resend failed, trying SMTP fallback...")

        # Fall back to SMTP
        if self._send_smtp(to, subject, body, html):
            return True

        # Last resort: send summary via Telegram
        if self.telegram_bot_token:
            alert_id = self.telegram_chat_id or self.health_alert_chat_id
            if alert_id:
                self._send_telegram(f"⚠️ Email delivery failed\nTo: {to}\nSubject: {subject}\n\n{body[:500]}")
        
        return False

    def send_to_admins(self, subject: str, body: str = "", **kwargs) -> bool:
        """Send to all configured admin emails + Telegram alert."""
        if not self.admin_emails:
            logger.warning("No admin emails configured")
            return False

        success = True
        for admin in self.admin_emails:
            if not self.send(to=admin, subject=subject, body=body, **kwargs):
                success = False

        return success

    def send_telegram(self, message: str, chat_id: str = "") -> bool:
        """
        Send a Telegram message.
        
        Args:
            message: Text to send (supports HTML formatting)
            chat_id: Override default chat ID
        
        Returns:
            True if sent successfully
        """
        return self._send_telegram(message, chat_id)

    # ═══════════════════════════════════════════════════════════════════
    # PROVIDERS
    # ═══════════════════════════════════════════════════════════════════

    def _send_resend(self, to: str, subject: str, body: str, html: bool) -> bool:
        """Send via Resend API."""
        try:
            import requests

            response = requests.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {self.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": self.default_from,
                    "to": [to],
                    "subject": subject,
                    "html" if html else "text": body,
                },
                timeout=15,
            )

            if response.status_code in (200, 201, 202):
                logger.info("Email sent (Resend): to=%s subject='%s'", to, subject)
                return True
            else:
                logger.error("Resend returned %d: %s", response.status_code, response.text[:200])

        except Exception as exc:
            logger.error("Resend send failed: %s", exc)

        return False

    def _send_smtp(self, to: str, subject: str, body: str, html: bool) -> bool:
        """Send via SMTP (Gmail or custom server)."""
        if not self.smtp_username or not self.smtp_password:
            logger.error("SMTP not configured")
            return False

        try:
            msg = MIMEMultipart()
            msg["From"] = self.default_from
            msg["To"] = to
            msg["Subject"] = subject
            msg["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
            msg["Message-ID"] = f"<{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.kanyoza@{self.smtp_username.split('@')[-1]}>"

            content_type = "html" if html else "plain"
            msg.attach(MIMEText(body, content_type, "utf-8"))

            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                server.starttls()
                server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)

            logger.info("Email sent (SMTP): to=%s subject='%s'", to, subject)
            return True

        except smtplib.SMTPAuthenticationError:
            logger.error("SMTP authentication failed")
        except smtplib.SMTPConnectError:
            logger.error("SMTP connection failed")
        except Exception as exc:
            logger.error("SMTP send failed: %s", exc)

        return False

    def _send_telegram(self, message: str, chat_id: str = "") -> bool:
        """Send message via Telegram Bot API."""
        try:
            import requests

            token = self.telegram_bot_token
            target_chat = chat_id or self.telegram_chat_id or self.health_alert_chat_id

            if not token or not target_chat:
                logger.debug("Telegram not configured")
                return False

            url = f"https://api.telegram.org/bot{token}/sendMessage"
            response = requests.post(
                url,
                json={
                    "chat_id": target_chat,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )

            if response.status_code == 200:
                logger.info("Telegram message sent")
                return True
            else:
                logger.error("Telegram returned %d: %s", response.status_code, response.text[:200])

        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)

        return False


# ═══════════════════════════════════════════════════════════════════════════
# PREDEFINED NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════

class KanyozaNotifications:
    """
    Pre-built notifications for common events.
    Used by Condition Engine and other services.
    """

    def __init__(self, email_service: EmailService):
        self.email = email_service

    def alert(self, title: str, message: str):
        """Send alert via all channels."""
        self.email.send_to_admins(subject=title, body=message)
        self.email.send_telegram(f"<b>🚨 {title}</b>\n\n{message}")

    def director_alert(self, payload: dict):
        """Format and send a Director alert."""
        alert_type = payload.get("type", "Unknown")
        task = payload.get("task", {})
        description = task.get("description", "No description")
        reason = task.get("reason", "")
        
        message = f"<b>👔 Director: {alert_type}</b>\n{description}"
        if reason:
            message += f"\n\n<i>{reason}</i>"
        
        self.email.send_telegram(message)
        self.email.send_to_admins(subject=f"Director: {alert_type}", body=message.replace("<b>","").replace("</b>","").replace("<i>","").replace("</i>",""))

    def render_failure_cascade(self, error_count: int, brand_name: str = "") -> str:
        return f"""
        <div style="font-family: monospace; max-width: 600px; padding: 20px;">
            <h2 style="color: #ef4444;">⚠️ Render Failure Cascade</h2>
            <p><strong>{error_count}</strong> rendering failures detected in the last 10 minutes.</p>
            {f'<p>Affected brand: <strong>{brand_name}</strong></p>' if brand_name else ''}
            <p>The system has automatically switched to HCTI fallback.</p>
            <hr style="border-color: #27272a;">
            <p style="color: #71717a; font-size: 12px;">
                Kanyoza Condition Engine · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
            </p>
        </div>
        """

    def weekly_report(self, stats: Dict[str, Any]) -> str:
        return f"""
        <div style="font-family: monospace; max-width: 600px; padding: 20px;">
            <h2 style="color: #818cf8;">📊 Weekly Analytics Report</h2>
            <table style="width: 100%; border-collapse: collapse;">
                <tr><td style="padding: 8px; border-bottom: 1px solid #27272a;">Posts Published</td><td style="padding: 8px; border-bottom: 1px solid #27272a; text-align: right;"><strong>{stats.get('posts', 0)}</strong></td></tr>
                <tr><td style="padding: 8px; border-bottom: 1px solid #27272a;">Total Engagement</td><td style="padding: 8px; border-bottom: 1px solid #27272a; text-align: right;"><strong>{stats.get('engagement', 0):,}</strong></td></tr>
                <tr><td style="padding: 8px; border-bottom: 1px solid #27272a;">New Followers</td><td style="padding: 8px; border-bottom: 1px solid #27272a; text-align: right;"><strong>{stats.get('followers', 0)}</strong></td></tr>
                <tr><td style="padding: 8px; border-bottom: 1px solid #27272a;">Render Errors</td><td style="padding: 8px; border-bottom: 1px solid #27272a; text-align: right;"><strong>{stats.get('errors', 0)}</strong></td></tr>
            </table>
            <hr style="border-color: #27272a; margin-top: 20px;">
            <p style="color: #71717a; font-size: 12px;">
                Kanyoza Analytics · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
            </p>
        </div>
        """

    def trial_expiring(self, brand_name: str, days_left: int) -> str:
        return f"""
        <div style="font-family: monospace; max-width: 600px; padding: 20px;">
            <h2 style="color: #f59e0b;">⏰ Trial Expiring</h2>
            <p><strong>{brand_name}</strong> has <strong>{days_left} day(s)</strong> remaining in their trial.</p>
            <p>Posting will be paused when the trial ends.</p>
            <hr style="border-color: #27272a;">
            <p style="color: #71717a; font-size: 12px;">
                Kanyoza Condition Engine · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
            </p>
        </div>
        """

    def engagement_spike(self, brand_name: str, ratio: float) -> str:
        return f"""
        <div style="font-family: monospace; max-width: 600px; padding: 20px;">
            <h2 style="color: #22c55e;">🚀 Engagement Spike Detected</h2>
            <p><strong>{brand_name}</strong> just received <strong>{ratio:.1f}x</strong> normal engagement.</p>
            <p>Similar content has been automatically queued for publishing.</p>
            <hr style="border-color: #27272a;">
            <p style="color: #71717a; font-size: 12px;">
                Kanyoza Condition Engine · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
            </p>
        </div>
        """

    def new_brand_welcome(self, brand_name: str) -> str:
        return f"""
        <div style="font-family: monospace; max-width: 600px; padding: 20px;">
            <h2 style="color: #818cf8;">👋 Welcome to Kanyoza!</h2>
            <p>Hi <strong>{brand_name}</strong>,</p>
            <p>Your account is ready. Here's what happens next:</p>
            <ol>
                <li>Connect your social media accounts</li>
                <li>Add topics to your Knowledge Base</li>
                <li>We'll start generating and posting content automatically</li>
            </ol>
            <p>Your first posts will go live within 24 hours.</p>
            <hr style="border-color: #27272a;">
            <p style="color: #71717a; font-size: 12px;">
                Kanyoza Onboarding · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
            </p>
        </div>
        """


# ═══════════════════════════════════════════════════════════════════════════
# CONDITION ENGINE INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

def register_email_actions(condition_engine, email_service: EmailService):
    """
    Register email-related actions in the Condition Engine.
    Call this after initializing both services.
    """
    notifications = KanyozaNotifications(email_service)

    def send_email(value, params, context):
        to = params.get("to") or email_service.admin_emails[0] if email_service.admin_emails else None
        if not to:
            logger.warning("No recipient for email action")
            return

        subject = params.get("subject") or str(value)
        template = params.get("template", "")
        body = params.get("body", "")

        email_service.send(
            to=to,
            subject=subject,
            body=body,
            template=template,
            context=context,
        )

    def notify_admins_email(value, params, context):
        template = params.get("template", "")
        subject = params.get("subject") or str(value)
        body = params.get("body", "")

        if template == "render_failure":
            body = notifications.render_failure_cascade(
                error_count=context.get("error_count_10min", 0),
                brand_name=context.get("brand_name", ""),
            )
        elif template == "engagement_spike":
            body = notifications.engagement_spike(
                brand_name=context.get("brand_name", ""),
                ratio=context.get("engagement_ratio", 1.0),
            )
        elif template == "trial_expiring":
            body = notifications.trial_expiring(
                brand_name=context.get("brand_name", ""),
                days_left=context.get("days_until_trial_end", 0),
            )
        elif template == "weekly_report":
            body = notifications.weekly_report(context.get("stats", {}))

        email_service.send_to_admins(subject=subject, body=body)
        email_service.send_telegram(f"<b>📧 {subject}</b>\n\n{body[:500]}")

    # Register with Condition Engine
    if hasattr(condition_engine, '_executor'):
        condition_engine._executor.register("send_email", send_email)
        condition_engine._executor.register("notify_admins_email", notify_admins_email)
        logger.info("Email actions registered with Condition Engine")


# ═══════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

email_service = EmailService()
kanyoza_notifications = KanyozaNotifications(email_service)
