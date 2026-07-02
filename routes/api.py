"""
Analytics and Admin API v9.0
Complete REST API for the Kanyoza Control dashboard.
"""

import logging
from datetime import datetime, timezone
from flask import jsonify, request

from analytics.tracker import analytics
from scheduler.post_scheduler import scheduler
from core.memory import memory
from config.settings import settings
from utils.rate_limiter import rate_limiter

logger = logging.getLogger("kanyoza.routes.api")

_guardian_scheduler = None


def set_guardian_scheduler(sched):
    """Set the guardian scheduler reference from bot.py."""
    global _guardian_scheduler
    _guardian_scheduler = sched


def register_api_routes(app):
    """Register all API routes with the Flask app."""

    def _require_auth():
        api_key = request.headers.get("X-API-Key", "")
        if not api_key:
            return False
        owner_psid = settings.owner_psid
        api_secret = getattr(settings, 'api_secret', '')
        if owner_psid and api_key == owner_psid:
            return True
        if api_secret and api_key == api_secret:
            return True
        return False

    # ==================================================================
    # PUBLIC
    # ==================================================================

    @app.route("/status", methods=["GET"])
    def system_status():
        s = scheduler.get_status()
        return jsonify({
            "status": "online", "version": "9.0",
            "environment": settings.environment,
            "gemini": bool(settings.gemini_key),
            "facebook": settings.is_configured,
            "supabase": settings.supabase_enabled,
            "guardian": settings.guardian_configured,
            "hf_configured": bool(settings.hf_token),
            "scheduler": {"running": s["running"], "queue_size": s["queue_size"], "last_post": s["last_post"]},
            "uptime": str(datetime.now(timezone.utc)),
        })

    @app.route("/metrics", methods=["GET"])
    def metrics():
        stats = analytics.get_stats()
        o = stats.get('overview', {})
        lines = [
            "# HELP kanyoza_messages_total Total messages",
            "# TYPE kanyoza_messages_total counter",
            f"kanyoza_messages_total{{type=\"received\"}} {o.get('messages_received', 0)}",
            f"kanyoza_messages_total{{type=\"sent\"}} {o.get('messages_sent', 0)}",
            "# HELP kanyoza_posts_total Total posts",
            "# TYPE kanyoza_posts_total counter",
            f"kanyoza_posts_total{{status=\"published\"}} {o.get('posts_published', 0)}",
            "# HELP kanyoza_users_active Active users 24h",
            "# TYPE kanyoza_users_active gauge",
            f"kanyoza_users_active {o.get('active_users_24h', 0)}",
            "# HELP kanyoza_errors_total Total errors",
            "# TYPE kanyoza_errors_total counter",
            f"kanyoza_errors_total {o.get('total_errors', 0)}",
        ]
        return "\n".join(lines), 200, {"Content-Type": "text/plain"}

    # ==================================================================
    # DASHBOARD
    # ==================================================================

    @app.route("/api/dashboard", methods=["GET"])
    def dashboard_stats():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        stats = analytics.get_stats()
        s = scheduler.get_status()
        return jsonify({
            "messages_today": stats.get('overview', {}).get('messages_received', 0),
            "posts_published": stats.get('overview', {}).get('posts_published', 0),
            "active_chats": stats.get('overview', {}).get('active_users_24h', 0),
            "guardian_issues": stats.get('overview', {}).get('guardian_critical', 0),
            "scheduler_running": s.get('running', False),
            "last_post": s.get('last_post'),
        })

    # ==================================================================
    # POSTS
    # ==================================================================

    @app.route("/api/posts", methods=["GET"])
    def get_posts():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                result = supabase_service.client.table('post_history').select('*').order('published_at', desc=True).limit(50).execute()
                return jsonify(result.data if result.data else [])
        except Exception as e:
            logger.error(f"Failed to fetch posts: {e}")
        return jsonify([])

    @app.route("/api/posts/create", methods=["POST"])
    def create_post():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(silent=True) or {}
        topic = data.get("topic", "")
        template = data.get("template", "deep_dive")

        if not topic:
            return jsonify({"error": "Topic is required"}), 400

        try:
            from services.content_engine import content_engine
            from services.facebook import facebook

            caption, image_bytes = content_engine.generate_post()
            if caption:
                success = facebook.post_to_page(caption, image_bytes=image_bytes)
                if success:
                    return jsonify({"status": "published", "words": len(caption.split())})
            return jsonify({"error": "Failed to publish"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/posts/<post_id>", methods=["DELETE"])
    def delete_post(post_id):
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                supabase_service.client.table('post_history').delete().eq('id', post_id).execute()
                return jsonify({"status": "deleted"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ==================================================================
    # MESSAGES
    # ==================================================================

    @app.route("/api/messages", methods=["GET"])
    def get_conversations():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                result = supabase_service.client.table('conversation_history').select('sender_id, text, role, created_at').order('created_at', desc=True).limit(100).execute()
                return jsonify(result.data if result.data else [])
        except Exception as e:
            logger.error(f"Failed to fetch messages: {e}")
        return jsonify([])

    @app.route("/api/messages/<sender_id>", methods=["GET"])
    def get_conversation(sender_id):
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                result = supabase_service.client.table('conversation_history').select('*').eq('sender_id', sender_id).order('created_at', desc=True).limit(50).execute()
                return jsonify(result.data if result.data else [])
        except Exception as e:
            return jsonify([])

    @app.route("/api/messages/reply", methods=["POST"])
    def reply_to_message():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(silent=True) or {}
        sender_id = data.get("sender_id", "")
        message = data.get("message", "")
        if not sender_id or not message:
            return jsonify({"error": "sender_id and message required"}), 400

        try:
            from services.facebook import facebook
            if facebook.send_message(sender_id, message):
                memory.add_message(sender_id, "assistant", message)
                return jsonify({"status": "sent"})
            return jsonify({"error": "Failed to send"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ==================================================================
    # CONTACTS
    # ==================================================================

    @app.route("/api/contacts", methods=["GET"])
    def get_contacts():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                result = supabase_service.client.table('contact_profiles').select('*').order('last_seen', desc=True).limit(50).execute()
                return jsonify(result.data if result.data else [])
        except Exception:
            pass
        return jsonify([])

    # ==================================================================
    # BOT CONTROL
    # ==================================================================

    @app.route("/api/bot/force-post", methods=["POST"])
    def force_post():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        return jsonify({"status": "queued" if scheduler.force_post() else "failed"})

    @app.route("/api/bot/force-news", methods=["POST"])
    def force_news():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        category = (request.get_json() or {}).get('category', None) if request.is_json else None
        return jsonify({"status": "queued" if scheduler.force_news(category) else "failed"})

    @app.route("/api/bot/force-engagement", methods=["POST"])
    def force_engagement():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        return jsonify({"status": "queued" if scheduler.force_engagement() else "failed"})

    # ==================================================================
    # FEATURES
    # ==================================================================

    @app.route("/api/features", methods=["GET"])
    def get_features():
        return jsonify({
            "auto_reply": getattr(settings, 'auto_reply_enabled', True),
            "auto_post": getattr(settings, 'auto_post_enabled', True),
            "auto_comment_reply": getattr(settings, 'auto_comment_reply', True),
            "cross_engage": getattr(settings, 'cross_engage_enabled', False),
            "guardian_scan": settings.guardian_configured,
            "thumbnails": bool(settings.hf_token),
        })

    @app.route("/api/features/toggle", methods=["POST"])
    def toggle_feature():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json(silent=True) or {}
        feature = data.get("feature", "")
        enabled = data.get("enabled", True)
        if feature not in ['auto_reply', 'auto_post', 'auto_comment_reply', 'cross_engage']:
            return jsonify({"error": "Invalid feature"}), 400
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                supabase_service.client.table('settings').upsert({'key': f'feature_{feature}', 'value': str(enabled).lower(), 'updated_at': datetime.now(timezone.utc).isoformat()}, on_conflict='key').execute()
        except Exception:
            pass
        return jsonify({"feature": feature, "enabled": enabled})

    # ==================================================================
    # SCHEDULE
    # ==================================================================

    @app.route("/api/schedule", methods=["GET"])
    def get_schedule():
        return jsonify({"content_hours_utc": settings.content_posting_hours_utc, "news_hours_utc": settings.news_posting_hours_utc})

    @app.route("/api/schedule", methods=["PUT"])
    def update_schedule():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json(silent=True) or {}
        def _validate_hours(raw):
            """Convert to ints and reject any value outside 0-23."""
            result = [int(h) for h in raw]
            if any(h < 0 or h > 23 for h in result):
                raise ValueError(f"Hours must be 0-23, got {result}")
            return result
        try:
            if data.get("content_hours_utc"):
                hours = _validate_hours(data["content_hours_utc"])
                settings.content_posting_hours_utc = hours
                scheduler.content_hours_utc = hours
            if data.get("news_hours_utc"):
                hours = _validate_hours(data["news_hours_utc"])
                settings.news_posting_hours_utc = hours
                scheduler.news_hours_utc = hours
        except (ValueError, TypeError) as e:
            return jsonify({"error": f"Invalid hours value: {e}"}), 400
        return jsonify({"status": "updated"})

    # ==================================================================
    # GUARDIAN
    # ==================================================================

    @app.route("/api/guardian/scan", methods=["POST"])
    def trigger_guardian_scan():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        global _guardian_scheduler
        if _guardian_scheduler:
            _guardian_scheduler.run_now()
            return jsonify({"status": "scan_triggered"})
        return jsonify({"status": "scan_requested", "note": "Will run on next cycle"})

    @app.route("/api/guardian/results", methods=["GET"])
    def get_guardian_results():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                result = supabase_service.client.table('scan_history').select('*').order('scanned_at', desc=True).limit(10).execute()
                return jsonify(result.data if result.data else [])
        except Exception:
            pass
        return jsonify([])

    # ==================================================================
    # ANALYTICS
    # ==================================================================

    @app.route("/api/analytics", methods=["GET"])
    def get_analytics():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        return jsonify(analytics.get_stats())

    @app.route("/api/analytics/performance", methods=["GET"])
    def get_post_performance():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                result = supabase_service.client.table('post_history').select('*').order('published_at', desc=True).limit(20).execute()
                return jsonify(result.data if result.data else [])
        except Exception:
            pass
        return jsonify([])

    # ==================================================================
    # TENANTS
    # ==================================================================

    @app.route("/api/tenants", methods=["GET"])
    def get_tenants():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                result = supabase_service.client.table('tenants').select('*').eq('status', 'active').execute()
                return jsonify(result.data if result.data else [])
        except Exception:
            pass
        return jsonify([])

    @app.route("/api/tenants/switch", methods=["POST"])
    def switch_tenant():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        return jsonify({"status": "switched", "tenant_id": (request.get_json(silent=True) or {}).get("tenant_id", "")})

    # ==================================================================
    # RATE LIMITS
    # ==================================================================

    @app.route("/api/rate-limits", methods=["GET"])
    def get_rate_limits():
        return jsonify(rate_limiter.get_config())

    @app.route("/api/unblock", methods=["POST"])
    def unblock_user():
        if not _require_auth():
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json(silent=True) or {}
        identifier = data.get('identifier', '')
        if not identifier:
            return jsonify({"error": "Missing identifier"}), 400
        return jsonify({"unblocked": rate_limiter.unblock(identifier)})
