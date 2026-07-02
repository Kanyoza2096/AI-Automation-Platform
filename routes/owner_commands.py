"""
Owner command handler — special slash commands only the bot owner can send.
Commands are evaluated before the main AI pipeline runs.
"""

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("kanyoza.routes.owner_commands")

COMMANDS = {
    "/help": "List available commands",
    "/status": "Bot status and configuration summary",
    "/analytics": "Post and message statistics",
    "/post": "Force publish a content post now",
    "/news": "Force publish a news post now",
    "/reload": "Hot-reload environment settings",
    "/mood": "Show current persona mood",
    "/ping": "Liveness check",
    "/scan": "Run Code Guardian security scan (if configured)",
}


class OwnerCommands:
    def __init__(
        self,
        facebook_service=None,
        analytics=None,
        post_scheduler=None,
        guardian_runner=None,
        settings=None,
        persona=None,
    ):
        self._fb = facebook_service
        self._analytics = analytics
        self._post_scheduler = post_scheduler
        self._guardian = guardian_runner
        self._settings = settings
        self._persona = persona

    def is_owner(self, sender_id: str, settings) -> bool:
        if not settings or not settings.owner_psid:
            return False
        return sender_id == settings.owner_psid

    def handle(self, sender_id: str, text: str) -> bool:
        """
        If text is a slash command, execute it and return True.
        Returns False if the text is not a recognized command.
        """
        if not text.startswith("/"):
            return False

        cmd = text.strip().lower().split()[0]
        args = text.strip()[len(cmd):].strip()

        handler = {
            "/help": self._cmd_help,
            "/status": self._cmd_status,
            "/analytics": self._cmd_analytics,
            "/post": self._cmd_post,
            "/news": self._cmd_news,
            "/reload": self._cmd_reload,
            "/mood": self._cmd_mood,
            "/ping": self._cmd_ping,
            "/scan": self._cmd_scan,
        }.get(cmd)

        if handler:
            try:
                handler(sender_id, args)
            except Exception as exc:
                logger.error("Owner command '%s' failed: %s", cmd, exc)
                self._reply(sender_id, f"⚠️ Command failed: {exc}")
            return True

        self._reply(sender_id, f"Unknown command: {cmd}\nType /help for available commands.")
        return True

    # ------------------------------------------------------------------
    # Command implementations
    # ------------------------------------------------------------------

    def _cmd_help(self, sender_id: str, _args: str) -> None:
        lines = ["🤖 Owner commands:\n"]
        for cmd, desc in COMMANDS.items():
            lines.append(f"{cmd} — {desc}")
        self._reply(sender_id, "\n".join(lines))

    def _cmd_ping(self, sender_id: str, _args: str) -> None:
        self._reply(sender_id, f"✅ Pong! {datetime.now(timezone.utc).strftime('%H:%M UTC')}")

    def _cmd_status(self, sender_id: str, _args: str) -> None:
        settings = self._settings
        if not settings:
            self._reply(sender_id, "Settings not available.")
            return
        cfg = settings.to_safe_dict()
        lines = [
            f"🔧 Bot Status — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            f"Gemini: {'✅' if cfg['gemini_key_configured'] else '❌'}  |  FB: {'✅' if cfg['page_token_configured'] else '❌'}",
            f"Supabase: {'✅' if cfg['supabase_enabled'] else '❌'}  |  HF: {'✅' if cfg['hf_configured'] else '❌'}",
            f"Guardian: {'✅' if cfg['guardian_enabled'] else '❌'}  |  WA: {'✅' if cfg['whatsapp_configured'] else '❌'}",
        ]
        if self._persona:
            lines.append(f"Mood: {self._persona.current_mood}")
        self._reply(sender_id, "\n".join(lines))

    def _cmd_analytics(self, sender_id: str, _args: str) -> None:
        if not self._analytics:
            self._reply(sender_id, "Analytics not configured.")
            return
        stats = self._analytics.stats()
        lines = [
            f"📊 Analytics",
            f"Messages: {stats['messages_total']}",
            f"Unique users: {stats['unique_users']}",
            f"Posts published: {stats['posts_total']}",
            f"News posts: {stats['news_posts_total']}",
            f"Errors: {stats['errors_total']}",
            f"Uptime: {int(stats['uptime_seconds'] / 3600)}h",
        ]
        self._reply(sender_id, "\n".join(lines))

    def _cmd_post(self, sender_id: str, _args: str) -> None:
        if not self._post_scheduler:
            self._reply(sender_id, "Post scheduler not configured.")
            return
        self._reply(sender_id, "🚀 Publishing post now...")
        threading.Thread(
            target=self._run_post_now,
            args=(sender_id,),
            daemon=True,
        ).start()

    def _run_post_now(self, sender_id: str) -> None:
        try:
            result = self._post_scheduler.post_now()
            self._reply(sender_id, f"✅ Post published: {result}")
        except Exception as exc:
            self._reply(sender_id, f"❌ Post failed: {exc}")

    def _cmd_news(self, sender_id: str, _args: str) -> None:
        if not self._post_scheduler:
            self._reply(sender_id, "Scheduler not configured.")
            return
        self._reply(sender_id, "📰 Publishing news post now...")
        threading.Thread(
            target=self._run_news_now,
            args=(sender_id,),
            daemon=True,
        ).start()

    def _run_news_now(self, sender_id: str) -> None:
        try:
            result = self._post_scheduler.post_news_now()
            self._reply(sender_id, f"✅ News post published: {result}")
        except Exception as exc:
            self._reply(sender_id, f"❌ News post failed: {exc}")

    def _cmd_reload(self, sender_id: str, _args: str) -> None:
        if not self._settings:
            self._reply(sender_id, "Settings not available.")
            return
        self._settings.reload()
        self._reply(sender_id, "✅ Settings reloaded from environment.")

    def _cmd_mood(self, sender_id: str, _args: str) -> None:
        if not self._persona:
            self._reply(sender_id, "Persona not configured.")
            return
        self._reply(sender_id, f"🎭 Current mood: {self._persona.current_mood}")

    def _cmd_scan(self, sender_id: str, _args: str) -> None:
        if not self._guardian:
            self._reply(sender_id, "Code Guardian not configured (check GUARDIAN_ENABLED and GitHub credentials).")
            return
        self._reply(sender_id, "🔍 Starting security scan... This may take a few minutes.")
        threading.Thread(
            target=self._run_scan,
            args=(sender_id,),
            daemon=True,
        ).start()

    def _run_scan(self, sender_id: str) -> None:
        try:
            result = self._guardian()
            self._reply(sender_id, f"✅ Scan complete: {result}")
        except Exception as exc:
            self._reply(sender_id, f"❌ Scan failed: {exc}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reply(self, sender_id: str, text: str) -> None:
        if self._fb:
            self._fb.send_message(sender_id, text)
        else:
            logger.info("Owner reply (no FB): %s", text)
