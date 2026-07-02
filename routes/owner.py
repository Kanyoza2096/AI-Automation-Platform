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
            "!test":      self.cmd_test,
            "!status":    self.cmd_status,
            "!users":     self.cmd_users,
            "!health":    self.cmd_health,
            "!help":      self.cmd_help,
            "!post":      self.cmd_post,
            "!news":      self.cmd_news,
            "!technews":  self.cmd_technews,
            "!malawinews": self.cmd_malawinews,
            "!trending":  self.cmd_trending,
            "!engage":    self.cmd_engage,
            "!pause":     self.cmd_pause,
            "!resume":    self.cmd_resume,
            "!memory":    self.cmd_memory,
            "!topics":    self.cmd_topics,
            "!version":   self.cmd_version,
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
            "📊 Info: !test !status !users !health !version\n"
            "📝 Content: !post !news !technews !malawinews !trending\n"
            "💬 Engage: !engage !pause !resume\n"
            "🧠 Memory: !memory !topics\n\n"
            "Slash: /help /status /analytics /post /news /mood /ping /scan"
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

    # ==========================================
    # MEMORY & TOPICS
    # ==========================================

    def cmd_memory(self, sender_id: str):
        """Show memory summary for the owner's own profile."""
        try:
            profile = memory.get_user_profile(sender_id)
            recent = memory.get_recent_messages(sender_id, limit=3)
            name = profile.get("name") or "unknown"
            interests = profile.get("interests", [])
            msg_count = profile.get("message_count", 0)
            lang = profile.get("communication_style", "unknown")
            msg = (
                f"🧠 MEMORY — you ({sender_id[:10]}...)\n\n"
                f"Name: {name}\n"
                f"Language: {lang}\n"
                f"Total messages: {msg_count}\n"
                f"Interests: {', '.join(interests[:5]) if interests else 'none detected'}\n"
                f"Recent turns: {len(recent)}\n"
            )
            facebook.send_message(sender_id, msg)
        except Exception as e:
            logger.error(f"Memory command error: {e}")
            facebook.send_message(sender_id, f"Memory read failed: {e}")

    def cmd_topics(self, sender_id: str):
        """List all available topic categories and their counts."""
        try:
            from services.content_engine import TOPICS, content_engine
            lines = ["📚 TOPIC CATEGORIES\n"]
            for cat, topics in TOPICS.items():
                lines.append(f"• {cat.replace('_', ' ').title()}: {len(topics)} topics")
            lines.append(f"\nTotal topics: {sum(len(v) for v in TOPICS.values())}")
            lines.append(f"Posts generated this session: {content_engine.post_counter}")
            lines.append(f"Last topic: {content_engine.last_topic[:60] or 'none'}")
            facebook.send_message(sender_id, "\n".join(lines))
        except Exception as e:
            logger.error(f"Topics command error: {e}")
            facebook.send_message(sender_id, f"Topics read failed: {e}")

    def cmd_version(self, sender_id: str):
        """Show platform version and architecture summary."""
        import sys
        try:
            sched_status = scheduler.get_status()
            running = sched_status.get("running", False)
        except Exception:
            running = False
        msg = (
            f"🚀 Kanyoza Systems AI Platform\n\n"
            f"Version: v10.0\n"
            f"Python: {sys.version.split()[0]}\n"
            f"Architecture: Flask + Gunicorn\n"
            f"AI: Google Gemini\n"
            f"Pipeline: Structured JSON → TechPost model → Facebook\n"
            f"Scheduler: {'running ✅' if running else 'paused ⏸️'}\n"
            f"Card themes: comparison, tutorial, code_tips, architecture\n"
            f"Owner commands: 16 (!bang) + 9 (/slash)"
        )
        facebook.send_message(sender_id, msg)


owner_commands = OwnerCommands()
