"""
Page Growth Service v2.0 — Database-Backed
- Persists invited users and engaged posts
- Survives restarts
"""

import random
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Set, Optional

from services.facebook import facebook
from services.gemini import gemini

logger = logging.getLogger("kanyoza.services.growth")

RELATED_PAGES = []

MAX_CROSS_ENGAGE_PER_CYCLE = 3
MIN_CROSS_ENGAGE_INTERVAL = timedelta(hours=6)


class GrowthService:
    """Database-backed growth service."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self.invited_users: Set[str] = set()
        self.last_cross_engage: Optional[datetime] = None
        self.engaged_posts: Set[str] = set()
        self._load_state()

    def _load_state(self):
        """Load state from Supabase."""
        if not self.supabase:
            return
        try:
            result = self.supabase.table('invited_users').select('user_id').execute()
            if result.data:
                self.invited_users = {row['user_id'] for row in result.data}

            result = self.supabase.table('engaged_posts').select('post_id').execute()
            if result.data:
                self.engaged_posts = {row['post_id'] for row in result.data}
        except Exception as e:
            logger.error(f"Failed to load growth state: {e}")

    def invite_commenter(self, user_id: str, username: str) -> bool:
        """Invite a user to follow the page."""
        if user_id in self.invited_users:
            return False

        success = facebook.invite_to_follow(user_id)
        if success:
            self.invited_users.add(user_id)

            if self.supabase:
                try:
                    self.supabase.table('invited_users').upsert({
                        'id': f"inv-{user_id}",
                        'user_id': user_id,
                        'username': username,
                        'invited_at': datetime.now(timezone.utc).isoformat(),
                    }).execute()
                except Exception as e:
                    logger.error(f"Failed to persist invited user: {e}")

        return success

    def cross_engage(self) -> dict:
        """Engage with related pages."""
        if not RELATED_PAGES:
            return {"liked": 0, "commented": 0}

        if self.last_cross_engage and datetime.now(timezone.utc) - self.last_cross_engage < MIN_CROSS_ENGAGE_INTERVAL:
            return {"liked": 0, "commented": 0}

        self.last_cross_engage = datetime.now(timezone.utc)
        stats = {"liked": 0, "commented": 0}
        engaged_this_cycle = 0
        random.shuffle(RELATED_PAGES)

        for page_id in RELATED_PAGES:
            if engaged_this_cycle >= MAX_CROSS_ENGAGE_PER_CYCLE:
                break
            try:
                posts = facebook.get_page_posts(page_id, limit=2)
                for post in posts:
                    post_id = post.get("id", "")
                    if post_id in self.engaged_posts:
                        continue

                    if random.random() < 0.5 and facebook.like_post(post_id):
                        stats["liked"] += 1
                        self._mark_engaged(post_id, page_id, 'liked')
                        engaged_this_cycle += 1
                        time.sleep(2)

                    message = post.get("message", "")
                    if message and len(message) > 30 and random.random() < 0.3:
                        comment = self._generate_engagement_comment(message)
                        if comment and facebook.comment_on_post(post_id, comment):
                            stats["commented"] += 1
                            self._mark_engaged(post_id, page_id, 'commented')
                            engaged_this_cycle += 1
                            time.sleep(3)
                time.sleep(1)
            except Exception as e:
                logger.error(f"Cross-engage error: {e}")

        if stats["liked"] > 0 or stats["commented"] > 0:
            logger.info(f"Cross-engage: {stats}")
        return stats

    def _mark_engaged(self, post_id: str, page_id: str, action: str):
        """Mark post as engaged in DB and memory."""
        self.engaged_posts.add(post_id)
        if self.supabase:
            try:
                self.supabase.table('engaged_posts').upsert({
                    'id': f"eng-{post_id}",
                    'post_id': post_id,
                    'page_id': page_id,
                    'action': action,
                    'engaged_at': datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception as e:
                logger.error(f"Failed to persist engaged post: {e}")

    def _generate_engagement_comment(self, post_text: str) -> str:
        """Generate a thoughtful comment."""
        try:
            prompt = (
                "You are a knowledgeable software engineer from Malawi. "
                "Leave a genuine, thoughtful 1-2 sentence comment on this post. "
                "Be authentic. No emojis. Don't mention your own page.\n\n"
                f"POST: {post_text[:500]}\n\nComment:"
            )
            comment = gemini._call_gemini(prompt=prompt, temperature=0.85, max_output_tokens=80, top_p=0.95, timeout=15)
            comment = comment.strip().strip('"').strip("'")
            return comment if len(comment) > 10 and len(comment) < 300 else None
        except Exception:
            return None


growth = GrowthService()
