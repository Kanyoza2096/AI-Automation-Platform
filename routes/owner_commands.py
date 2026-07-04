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
    "/ratelimit": "Show Gemini AI quota status and seconds until reset",
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
            "/ratelimit": self._cmd_ratelimit,
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
            f"Gemini: {'✅' if cfg.get('gemini_key_configured') else '❌'}  |  FB: {'✅' if cfg.get('facebook_configured') else '❌'}",
            f"Supabase: {'✅' if cfg.get('supabase_enabled') else '❌'}  |  HF: {'✅' if cfg.get('hf_configured') else '❌'}",
            f"Guardian: {'✅' if cfg.get('guardian_enabled') else '❌'}  |  WA: {'✅' if cfg.get('whatsapp_configured') else '❌'}",
        ]
        if self._persona:
            lines.append(f"Mood: {self._persona.current_mood}")
        self._reply(sender_id, "\n".join(lines))

    def _cmd_analytics(self, sender_id: str, _args: str) -> None:
        if not self._analytics:
            self._reply(sender_id, "Analytics not configured.")
            return
        stats = self._analytics.get_stats()
        overview = stats.get("overview", {})
        lines = [
            f"📊 Analytics",
            f"Messages: {overview.get('messages_received', 0)}",
            f"Unique users: {overview.get('total_users', 0)}",
            f"Posts published: {overview.get('posts_published', 0)}",
            f"Errors: {overview.get('total_errors', 0)}",
            f"Active 24h: {overview.get('active_users_24h', 0)}",
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
            # Use run_content_now() so we block until the workflow finishes
            # and report the real result — not just "content post queued".
            if hasattr(self._post_scheduler, "run_content_now"):
                result = self._post_scheduler.run_content_now()
                if result.get("ok"):
                    topic = result.get("topic", "")
                    platforms = ", ".join(result.get("platforms", []))
                    duration = result.get("duration_ms", 0)
                    self._reply(
                        sender_id,
                        f"✅ Post published!\n"
                        f"📌 Topic: {topic}\n"
                        f"📤 Platform(s): {platforms or 'none'}\n"
                        f"⏱ {duration}ms",
                    )
                else:
                    error = result.get("error") or result.get("failed_steps") or "unknown error"
                    self._reply(sender_id, f"❌ Post failed: {error}")
            else:
                # Fallback to old queue-based path if method unavailable
                result = self._post_scheduler.post_now()
                self._reply(sender_id, f"✅ Post queued: {result}")
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

    def _cmd_ratelimit(self, sender_id: str, _args: str) -> None:
        import time as _time
        lines = ["⏱ Gemini AI Quota Status\n"]
        try:
            from services.gemini import gemini as _gemini, RATE_LIMIT_BACKOFF_SECONDS
            elapsed = _time.time() - _gemini._last_rate_limit_time
            if _gemini._last_rate_limit_time == 0.0:
                lines.append("✅ No rate limit hit since startup — quota is clear")
            elif elapsed >= RATE_LIMIT_BACKOFF_SECONDS:
                lines.append(f"✅ Quota clear (last hit {int(elapsed)}s ago)")
            else:
                remaining = int(RATE_LIMIT_BACKOFF_SECONDS - elapsed) + 1
                lines.append(f"🚫 Rate limited — resets in {remaining}s")
                lines.append(f"   (backoff window: {RATE_LIMIT_BACKOFF_SECONDS}s)")
            lines.append(f"   Last hit: {int(elapsed)}s ago" if _gemini._last_rate_limit_time else "")
        except Exception as exc:
            lines.append(f"⚠️ Could not read Gemini state: {exc}")

        try:
            from scheduler.post_scheduler import scheduler as _sched
            locked = not _sched._lock.acquire(blocking=False)
            if locked:
                lines.append("\n🔒 Publish lock: HELD (post in progress)")
            else:
                _sched._lock.release()
                lines.append("\n🔓 Publish lock: free")
            wf = "✅ wired" if _sched.workflow_factory is not None else "❌ not wired"
            lines.append(f"   Workflow factory: {wf}")
            if _sched.last_post_time:
                ago = int((_time.time() - _sched.last_post_time.timestamp()))
                lines.append(f"   Last post: {ago}s ago")
            else:
                lines.append("   Last post: never")
        except Exception as exc:
            lines.append(f"\n⚠️ Could not read scheduler state: {exc}")

        self._reply(sender_id, "\n".join(l for l in lines if l))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reply(self, sender_id: str, text: str) -> None:
        if self._fb:
            self._fb.send_message(sender_id, text)
        else:
            logger.info("Owner reply (no FB): %s", text)
