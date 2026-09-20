"""
Analytics Tracker v7.0 — Supabase Persistence
- Tracks all bot events to Supabase
- Provides stats for dashboard and API
- Survives restarts
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("kanyoza.analytics")


class AnalyticsTracker:
    """Tracks bot analytics with Supabase persistence."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client

    def _track(self, table: str, data: dict):
        if not self.supabase:
            return
        
        from core.workspace_context import get_current_workspace, get_current_brand
        
        ws_id = data.get("workspace_id") or get_current_workspace()
        br_id = data.get("brand_id") or get_current_brand()
        
        payload = {
            'id': uuid.uuid4().hex,
            **data,
            'workspace_id': ws_id,
            'brand_id': br_id,
            'created_at': datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            self.supabase.table(table).insert(payload).execute()
        except Exception as e:
            logger.error(f"Failed to track {table} event: {e}")

    def record_message_received(self, sender_id: str):
        self._track('message_events', {'sender_id': sender_id, 'event_type': 'received'})

    def record_message_sent(self, sender_id: str, response_time_s: float):
        """Record a sent message. response_time_s is elapsed wall-clock time in seconds."""
        self._track('message_events', {
            'sender_id': sender_id, 'event_type': 'sent',
            'response_time_ms': int(response_time_s * 1000),
        })

    def record_message_failed(self, sender_id: str, error: str):
        self._track('message_events', {'sender_id': sender_id, 'event_type': 'failed'})
        self._track('error_events', {
            'service': 'messenger', 'error_type': 'message_failed',
            'error_message': error[:500], 'context': f"Failed to send to {sender_id}",
        })

    def record_post_published(self, topic: str, category: str, template: str,
                               word_count: int, thumbnail: bool, attempts: int):
        self._track('post_events', {
            'event_type': 'published', 'topic': topic, 'category': category,
            'template_type': template, 'word_count': word_count,
            'thumbnail_generated': thumbnail, 'generation_attempts': attempts,
        })

    def record_post_failed(self, topic: str, category: str, reason: str):
        self._track('post_events', {'event_type': 'failed', 'topic': topic, 'category': category})
        self._track('error_events', {
            'service': 'content_engine', 'error_type': 'post_failed',
            'error_message': reason[:500], 'context': f"Topic: {topic[:100]}",
        })

    def record_post_fallback_used(self, topic: str):
        self._track('post_events', {'event_type': 'fallback_used', 'topic': topic})

    def record_comment_replied(self, post_id: str, commenter_name: str, success: bool):
        self._track('engagement_events', {
            'event_type': 'comment_replied', 'post_id': post_id,
            'username': commenter_name, 'success': success,
        })

    def record_user_invited(self, user_id: str, username: str, success: bool):
        self._track('engagement_events', {
            'event_type': 'user_invited', 'user_id': user_id,
            'username': username, 'success': success,
        })

    def record_cross_engage(self, action: str, page_id: str, success: bool):
        self._track('engagement_events', {
            'event_type': f'cross_engage_{action}',
            'target_page_id': page_id, 'success': success,
        })

    def record_error(self, service: str, error_type: str, message: str, context: str = ""):
        self._track('error_events', {
            'service': service, 'error_type': error_type,
            'error_message': message[:500], 'context': context[:500],
        })

    def record_guardian_scan(self, repos: int, files: int, issues: int,
                              critical: int, high: int, medium: int, low: int,
                              scan_type: str = 'scheduled'):
        if not self.supabase:
            return
        try:
            self.supabase.table('scan_history').insert({
                'id': 'scan-' + uuid.uuid4().hex[:16],
                'repos_scanned': repos, 'files_scanned': files,
                'total_issues': issues, 'critical': critical,
                'high': high, 'medium': medium, 'low': low,
                'scan_type': scan_type,
                'scanned_at': datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as e:
            logger.error(f"Failed to record scan: {e}")

    def get_recent_messages(self, limit: int = 50) -> list:
        """Return recent messages for the Messenger dashboard."""
        if self.supabase:
            try:
                result = (
                    self.supabase.table("conversation_history")
                    .select("*")
                    .order("created_at", desc=True)
                    .limit(limit)
                    .execute()
                )
                if result.data:
                    return result.data
            except Exception as e:
                logger.error(f"Failed to get recent messages: {e}")
        return []

    def get_recent_posts(self, limit: int = 50) -> list:
        """Return recent posts for the Posts dashboard."""
        if self.supabase:
            try:
                result = (
                    self.supabase.table("post_history")
                    .select("*")
                    .order("published_at", desc=True)
                    .limit(limit)
                    .execute()
                )
                if result.data:
                    return result.data
            except Exception as e:
                logger.error(f"Failed to get recent posts: {e}")
        return []

    def get_stats(self) -> dict:
        if self.supabase:
            try:
                result = self.supabase.rpc('get_bot_stats', {'p_days': 7}).execute()
                if result.data:
                    return result.data
            except Exception as e:
                logger.error(f"Failed to get stats: {e}")

        return {
            'overview': {
                'messages_received': 0, 'messages_sent': 0,
                'posts_published': 0, 'posts_failed': 0,
                'active_users_24h': 0, 'total_users': 0,
                'comments_replied': 0, 'users_invited': 0,
                'avg_response_time_ms': 0, 'total_errors': 0,
            },
            'daily': [], 'top_users': [], 'errors_by_service': [],
        }


analytics = AnalyticsTracker()
