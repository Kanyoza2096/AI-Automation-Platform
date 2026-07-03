"""
Kanyoza Systems AI Platform v10 — Application Factory.

Creates and configures the Flask application.
Zero business logic lives here — this module only wires middleware,
error handlers, and route blueprints to the Flask app instance.
"""

from __future__ import annotations

import logging
from typing import Optional

from flask import Flask, jsonify, request

from config.settings import Settings
from service_container import ServiceContainer
from utils.ids import set_correlation_id, get_current_correlation_id

logger = logging.getLogger("kanyoza.factory")


def create_app(
    settings: Settings,
    container: ServiceContainer,
    test_config: Optional[dict] = None,
) -> Flask:
    """
    Construct and return a configured Flask application.
    """
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # ------------------------------------------------------------------
    # Flask configuration
    # ------------------------------------------------------------------
    import os
    _secret_key = os.getenv("SESSION_SECRET", "")
    if not _secret_key:
        if settings.env.is_production:
            import sys
            logger.critical(
                "SESSION_SECRET is not set — refusing to start in production."
            )
            sys.exit(1)
        else:
            import secrets
            _secret_key = secrets.token_hex(32)
            logger.warning(
                "SESSION_SECRET not set — using ephemeral key (development only)."
            )

    app.config.update(
        ENV=settings.environment,
        TESTING=settings.env.is_testing,
        SECRET_KEY=_secret_key,
        JSON_SORT_KEYS=False,
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    app.extensions["service_container"] = container

    # ------------------------------------------------------------------
    # Middleware
    # ------------------------------------------------------------------
    _register_request_hooks(app)

    # ------------------------------------------------------------------
    # Error handlers
    # ------------------------------------------------------------------
    _register_error_handlers(app)

    # ------------------------------------------------------------------
    # System routes
    # ------------------------------------------------------------------
    _register_system_routes(app, settings)

    # ------------------------------------------------------------------
    # Application blueprints
    # ------------------------------------------------------------------
    _register_webhook_routes(app)
    _register_product_routes(app)
    _register_socketio(app)

    logger.info("Flask application created (env=%s)", settings.environment)
    return app


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

def _register_request_hooks(app: Flask) -> None:
    # ------------------------------------------------------------------
    # CORS — allow the React frontend (any origin) to call REST endpoints.
    # SocketIO already has cors_allowed_origins="*"; this covers HTTP routes.
    # ------------------------------------------------------------------
    _CORS_HEADERS = "Content-Type, Authorization, X-API-Key, X-Correlation-ID"
    _CORS_METHODS = "GET, POST, PUT, DELETE, OPTIONS"

    @app.before_request
    def handle_preflight():
        """Return 200 for CORS preflight OPTIONS requests immediately."""
        if request.method == "OPTIONS":
            resp = app.make_default_options_response()
            origin = request.headers.get("Origin", "*")
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Methods"] = _CORS_METHODS
            resp.headers["Access-Control-Allow-Headers"] = _CORS_HEADERS
            resp.headers["Access-Control-Max-Age"] = "86400"
            return resp

    @app.before_request
    def assign_correlation_id():
        incoming = request.headers.get("X-Correlation-ID")
        cid = set_correlation_id(incoming)
        request.environ["correlation_id"] = cid

    @app.after_request
    def add_cors_and_security_headers(response):
        # CORS
        origin = request.headers.get("Origin", "")
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = _CORS_METHODS
            response.headers["Access-Control-Allow-Headers"] = _CORS_HEADERS
        # Correlation ID
        cid = get_current_correlation_id()
        if cid:
            response.headers["X-Correlation-ID"] = cid
        # Security headers (relaxed X-Frame-Options so the console iframe can embed)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")

        # ── Live Payload Inspector: emit sanitized API summary to dashboard ──
        # Only emit for /api/v1/* routes; skip OPTIONS and SocketIO upgrades.
        if (
            request.method != "OPTIONS"
            and request.path.startswith("/api/v1/")
            and "text/event-stream" not in response.content_type
        ):
            try:
                from core.socket_server import emit_to_dashboard
                from datetime import datetime, timezone as _tz
                import json as _json

                # Build a safe request body summary — redact credential fields.
                req_keys: list = []
                try:
                    raw = request.get_data(as_text=True)
                    if raw:
                        parsed = _json.loads(raw)
                        if isinstance(parsed, dict):
                            _REDACT = {
                                "token", "password", "secret", "key", "api_key",
                                "access_token", "bearer", "authorization",
                                "system_prompt_override",   # may contain sensitive instructions
                            }
                            req_keys = [
                                k for k in parsed
                                if k.lower() not in _REDACT
                            ]
                except Exception:
                    pass

                # For response — emit only status + top-level keys, never values.
                resp_keys: list = []
                resp_ok: bool | None = None
                try:
                    if response.is_json or response.content_type.startswith("application/json"):
                        resp_parsed = _json.loads(response.get_data(as_text=True))
                        if isinstance(resp_parsed, dict):
                            resp_keys = list(resp_parsed.keys())[:20]
                            resp_ok = resp_parsed.get("ok")
                except Exception:
                    pass

                emit_to_dashboard("api_payload", {
                    "direction": "api",
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "ts": datetime.now(_tz.utc).isoformat(),
                    "request_keys": req_keys,   # field names only — no values
                    "response_keys": resp_keys,
                    "response_ok": resp_ok,
                    "correlation_id": cid or "",
                })
            except Exception:
                pass
        # ─────────────────────────────────────────────────────────────────────

        return response


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

def _register_error_handlers(app: Flask) -> None:
    from core.exceptions import KanyozaError

    @app.errorhandler(KanyozaError)
    def handle_kanyoza_error(exc: KanyozaError):
        status = getattr(exc, "http_status", 500)
        logger.error("KanyozaError: %s [%s]", exc.message, exc.error_code)
        return jsonify(exc.to_dict()), status

    @app.errorhandler(404)
    def handle_404(exc):
        return jsonify({"error": "NOT_FOUND", "message": "Resource not found."}), 404

    @app.errorhandler(405)
    def handle_405(exc):
        return jsonify({"error": "METHOD_NOT_ALLOWED", "message": "Method not allowed."}), 405

    @app.errorhandler(500)
    def handle_500(exc):
        logger.exception("Unhandled 500 error")
        return jsonify({"error": "INTERNAL_ERROR", "message": "An internal error occurred."}), 500


# ---------------------------------------------------------------------------
# System routes
# ---------------------------------------------------------------------------

def _register_system_routes(app: Flask, settings: Settings) -> None:
    @app.route("/")
    def root():
        return jsonify({
            "service": "Kanyoza Systems AI Platform",
            "version": "10.0",
            "status": "operational",
            "environment": settings.environment,
        })

    @app.route("/health")
    def health():
        return jsonify({
            "status": "healthy",
            "version": "10.0",
            "ai_provider": settings.ai_provider,
            "supabase": settings.supabase_enabled,
        })

    @app.route("/status")
    def status_legacy():
        """Legacy /status endpoint — returns online flag with service availability."""
        return jsonify({
            "status": "online",
            "version": "10.0",
            "gemini": bool(getattr(settings, "gemini_key", "")),
            "facebook": bool(getattr(settings, "page_access_token", "")),
            "supabase": settings.supabase_enabled,
            "guardian": settings.guardian_configured,
        })

    @app.route("/api/v1/status")
    def status():
        return jsonify({
            "version": "10.0",
            "config": settings.to_safe_dict(),
        })

    @app.route("/api/v1/health/deep")
    def health_deep():
        import time
        results = {}
        overall = "ok"

        # Gemini
        gemini_key = getattr(settings, "gemini_key", "") or ""
        if not gemini_key:
            results["gemini"] = {"status": "skip", "reason": "GEMINI_KEY not set"}
        else:
            try:
                from services.gemini import gemini as gemini_svc
                t0 = time.monotonic()
                reply = gemini_svc._call(prompt="Reply with exactly one word: ok", temperature=0.0, max_tokens=8)
                ms = round((time.monotonic() - t0) * 1000)
                results["gemini"] = {"status": "ok", "latency_ms": ms} if reply else {"status": "error", "reason": "empty response"}
            except Exception as exc:
                results["gemini"] = {"status": "error", "reason": str(exc)[:120]}
                overall = "degraded"

        # Facebook
        fb_token = getattr(settings, "page_access_token", "") or ""
        if not fb_token:
            results["facebook"] = {"status": "skip", "reason": "PAGE_ACCESS_TOKEN not set"}
        else:
            try:
                import requests as _req
                t0 = time.monotonic()
                r = _req.get("https://graph.facebook.com/me", params={"fields": "id,name", "access_token": fb_token}, timeout=8)
                ms = round((time.monotonic() - t0) * 1000)
                if r.status_code == 200:
                    data = r.json()
                    results["facebook"] = {"status": "ok", "latency_ms": ms, "page_id": data.get("id"), "page_name": data.get("name")}
                else:
                    results["facebook"] = {"status": "error", "http": r.status_code}
                    overall = "degraded"
            except Exception as exc:
                results["facebook"] = {"status": "error", "reason": str(exc)[:120]}
                overall = "degraded"

        return jsonify({"status": overall, "version": "10.0", "services": results})

    @app.route("/metrics")
    def prometheus_metrics():
        """Prometheus-compatible plaintext metrics."""
        try:
            from analytics.tracker import analytics as _analytics
            o = _analytics.get_stats().get("overview", {})
        except Exception:
            o = {}
        lines = [
            "# HELP kanyoza_messages_total Total messages",
            "# TYPE kanyoza_messages_total counter",
            f'kanyoza_messages_total{{type="received"}} {o.get("messages_received", 0)}',
            f'kanyoza_messages_total{{type="sent"}} {o.get("messages_sent", 0)}',
            "# HELP kanyoza_posts_total Total posts",
            "# TYPE kanyoza_posts_total counter",
            f'kanyoza_posts_total{{status="published"}} {o.get("posts_published", 0)}',
            "# HELP kanyoza_users_active Active users 24h",
            "# TYPE kanyoza_users_active gauge",
            f'kanyoza_users_active {o.get("active_users_24h", 0)}',
            "# HELP kanyoza_errors_total Total errors",
            "# TYPE kanyoza_errors_total counter",
            f'kanyoza_errors_total {o.get("total_errors", 0)}',
        ]
        return "\n".join(lines), 200, {"Content-Type": "text/plain"}


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def _register_webhook_routes(app: Flask) -> None:
    """Register the Facebook Messenger webhook routes."""
    try:
        from routes.webhook import register_webhook_routes
        register_webhook_routes(app)
        logger.info("Webhook routes registered")
    except Exception as exc:
        logger.warning("Webhook route registration skipped: %s", exc)


def _register_product_routes(app: Flask) -> None:
    """Register product API blueprints."""
    try:
        from products.converter.routes import converter_bp
        app.register_blueprint(converter_bp)
        logger.info("Converter API registered at /api/v1")
    except Exception as exc:
        logger.warning("Converter blueprint skipped: %s", exc)


def _register_socketio(app: Flask) -> None:
    """Attach the SocketIO singleton to the Flask app and register the dashboard API."""
    try:
        from core.socket_server import init_app as socketio_init
        socketio_init(app)
    except Exception as exc:
        # SocketIO failure is not silent — WebSocket will be completely broken
        logger.error(
            "SocketIO init FAILED — WebSocket/real-time dashboard will not work: %s", exc
        )

    try:
        from api.dashboard import dashboard_bp
        app.register_blueprint(dashboard_bp)
        logger.info("Dashboard API registered at /api/v1/dashboard")
    except Exception as exc:
        logger.error("Dashboard blueprint registration FAILED: %s", exc)

    # M8: versioned REST API
    try:
        from api.v1 import api_v1_bp
        app.register_blueprint(api_v1_bp)
        logger.info("API v1 Blueprint registered at /api/v1/*")
    except Exception as exc:
        logger.error("API v1 blueprint registration FAILED: %s", exc)
