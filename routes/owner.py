"""
Owner command handlers v9.0
Special commands available only to the bot owner.
"""

import logging
import threading
import traceback
from typing import Dict, Callable

from services.facebook import facebook
from core.memory import memory
from analytics.tracker import analytics
from scheduler.post_scheduler import scheduler

logger = logging.getLogger("kanyoza.routes.owner")


class OwnerCommands:
    """Handles owner-only bot commands."""

    def __init__(self):
        self.commands: Dict[str, Callable] = {
            "!test": self.cmd_test,
            "!status": self.cmd_status,
            "!users": self.cmd_users,
            "!health": self.cmd_health,
            "!help": self.cmd_help,
            "!post": self.cmd_post,
            "!news": self.cmd_news,
            "!technews": self.cmd_technews,
            "!malawinews": self.cmd_malawinews,
            "!trending": self.cmd_trending,
            "!engage": self.cmd_engage,
            "!pause": self.cmd_pause,
            "!resume": self.cmd_resume,
        }

    def handle_command(self, sender_id: str, text: str) -> bool:
        command = text.lower().strip()
        if command not in self.commands:
            return False
        logger.info(f"Owner command: {command}")
        self.commands[command](sender_id)
        return True

    def cmd_test(self, sender_id: str):
        import datetime as dt
        facebook.send_message(sender_id,
            f"✅ Bot is alive!\n\nVersion: 9.0\n"
            f"Time: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )

    def cmd_status(self, sender_id: str):
        stats = analytics.get_stats()
        overview = stats.get('overview', {})
        sched = scheduler.get_status()
        msg = (
            f"📊 SYSTEM STATUS\n\n"
            f"💬 Messages: {overview.get('messages_received', 0)} in / {overview.get('messages_sent', 0)} out\n"
            f"👥 Active: {overview.get('active_users_24h', 0)}\n"
            f"📝 Posts: {overview.get('posts_published', 0)}\n"
            f"⚡ Avg Response: {overview.get('avg_response_time_ms', 0)}ms\n"
            f"🕐 Last Post: {sched.get('last_post', 'Never')}"
        )
        facebook.send_message(sender_id, msg)

    def cmd_users(self, sender_id: str):
        stats = analytics.get_stats()
        top_users = stats.get('top_users', [])[:5]
        if not top_users:
            facebook.send_message(sender_id, "No users yet.")
            return
        msg = "👥 Top Users:\n\n"
        for i, user in enumerate(top_users, 1):
            uid = str(user.get('id', user.get('sender_id', 'unknown')))[:10]
            count = user.get('messages', user.get('total_messages', 0))
            msg += f"{i}. {uid}... - {count} msgs\n"
        facebook.send_message(sender_id, msg)

    def cmd_health(self, sender_id: str):
        from config.settings import settings
        checks = {
            "Gemini": bool(settings.gemini_key),
            "Facebook": facebook.is_configured,
            "Supabase": settings.supabase_enabled,
        }
        msg = "🏥 HEALTH\n\n"
        for name, ok in checks.items():
            msg += f"{'✅' if ok else '❌'} {name}\n"
        facebook.send_message(sender_id, msg)

    def cmd_help(self, sender_id: str):
        facebook.send_message(sender_id,
            "🤖 COMMANDS\n\n"
            "!test !status !users !health\n"
            "!post !news !technews !malawinews !trending\n"
            "!engage !pause !resume !help"
        )

    # ==========================================
    # CONTENT
    # ==========================================

    def cmd_post(self, sender_id: str):
        facebook.send_message(sender_id, "⚙️ Generating post...")
        threading.Thread(target=self._do_force_post, args=(sender_id,), daemon=True).start()

    def _do_force_post(self, sender_id: str):
        try:
            from services.content_engine import content_engine
            from services.facebook import facebook as fb

            logger.info("Force post: generating content...")
            caption, image_bytes = content_engine.generate_post()

            if not caption:
                logger.error("Force post: caption is empty/None")
                facebook.send_message(sender_id, "❌ Failed to generate content (empty caption).")
                return

            logger.info(f"Force post: publishing ({len(caption.split())} words)...")
            success = fb.post_to_page(caption, image_bytes=image_bytes)

            if success:
                logger.info("Force post: published successfully!")
                facebook.send_message(sender_id, "✅ Post published!")
            else:
                logger.error("Force post: Facebook returned failure")
                facebook.send_message(sender_id, "❌ Failed to publish to Facebook.")
        except Exception as e:
            logger.error(f"❌ Force post crashed: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
            facebook.send_message(sender_id, f"❌ Error: {str(e)[:100]}")

    def cmd_news(self, sender_id: str):
        facebook.send_message(sender_id, "📰 Fetching news...")
        threading.Thread(target=self._do_force_news, args=(sender_id, None), daemon=True).start()

    def cmd_technews(self, sender_id: str):
        facebook.send_message(sender_id, "💻 Fetching tech news...")
        threading.Thread(target=self._do_force_news, args=(sender_id, "technology"), daemon=True).start()

    def cmd_malawinews(self, sender_id: str):
        facebook.send_message(sender_id, "🇲🇼 Fetching Malawi news...")
        threading.Thread(target=self._do_force_news, args=(sender_id, "malawi"), daemon=True).start()

    def cmd_trending(self, sender_id: str):
        facebook.send_message(sender_id, "🔥 Fetching trending...")
        threading.Thread(target=self._do_force_trending, args=(sender_id,), daemon=True).start()

    def _do_force_news(self, sender_id: str, category: str = None):
        try:
            success = scheduler.force_news(category)
            facebook.send_message(sender_id, f"{'✅' if success else '❌'} {category or 'News'} {'queued' if success else 'failed'}.")
        except Exception as e:
            logger.error(f"Force news error: {e}")

    def _do_force_trending(self, sender_id: str):
        try:
            from services.news_scraper import news_scraper
            success = news_scraper.post_trending()
            facebook.send_message(sender_id, f"{'✅' if success else '❌'} Trending.")
        except Exception as e:
            logger.error(f"Force trending error: {e}")

    def cmd_engage(self, sender_id: str):
        facebook.send_message(sender_id, "💬 Checking comments...")
        threading.Thread(target=self._do_force_engage, args=(sender_id,), daemon=True).start()

    def _do_force_engage(self, sender_id: str):
        try:
            scheduler.force_engagement()
        except Exception as e:
            logger.error(f"Force engage error: {e}")

    def cmd_pause(self, sender_id: str):
        try:
            scheduler._running = False
            facebook.send_message(sender_id, "Scheduler paused. Auto-posting stopped.")
        except Exception as e:
            logger.error(f"Pause error: {e}")
            facebook.send_message(sender_id, "Could not pause scheduler.")

    def cmd_resume(self, sender_id: str):
        try:
            if not scheduler._running:
                scheduler._running = True
                import threading
                scheduler._scheduler_thread = threading.Thread(
                    target=scheduler._run, daemon=False, name="scheduler-producer"
                )
                scheduler._scheduler_thread.start()
                facebook.send_message(sender_id, "Scheduler resumed. Auto-posting restarted.")
            else:
                facebook.send_message(sender_id, "Scheduler is already running.")
        except Exception as e:
            logger.error(f"Resume error: {e}")
            facebook.send_message(sender_id, "Could not resume scheduler.")


owner_commands = OwnerCommands()
