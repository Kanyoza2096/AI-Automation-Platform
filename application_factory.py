"""
Kanyoza Systems AI Platform v10 — Application Factory.

Creates and configures the Flask application.
Zero business logic lives here — this module only wires middleware,
error handlers, and route blueprints to the Flask app instance.

Follows the Flask application factory pattern so the app can be created
multiple times in tests with different configurations.
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

    Parameters
    ----------
    settings : Settings
        Application configuration (injected — not read from globals).
    container : ServiceContainer
        Fully-wired DI container.
    test_config : dict | None
        Optional Flask config overrides for the test environment.

    Returns
    -------
    Flask
        Ready-to-serve application instance.
    """
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # ------------------------------------------------------------------
    # Flask configuration
    # ------------------------------------------------------------------
    import os
    _secret_key = os.getenv("SESSION_SECRET", "")
    if not _secret_key:
        if settings.env.is_production:
            # Hard-fail in production — a missing signing key is a security
            # vulnerability, not a degradable condition.
            import sys
            logger.critical(
                "SESSION_SECRET is not set — refusing to start in production. "
                "Add it as a Replit Secret."
            )
            sys.exit(1)
        else:
            # Development only: generate a per-process ephemeral key so
            # sessions are invalidated on restart (acceptable in dev).
            import secrets
            _secret_key = secrets.token_hex(32)
            logger.warning(
                "SESSION_SECRET not set — using ephemeral key (development only). "
                "Sessions will not persist across restarts."
            )

    app.config.update(
        ENV=settings.environment,
        TESTING=settings.env.is_testing,
        SECRET_KEY=_secret_key,
        JSON_SORT_KEYS=False,
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,  # 16 MB max request body
    )
    if test_config:
        app.config.update(test_config)

    # Attach container to app for access from request context if needed
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
    # Health / root routes (inline — no business logic)
    # ------------------------------------------------------------------
    _register_system_routes(app, settings)

    # ------------------------------------------------------------------
    # Application blueprints
    # NOTE: v9 routes are registered here to keep the existing bot alive
    #       while v10 route blueprints are built in later milestones.
    # ------------------------------------------------------------------
    _register_v9_routes(app)

    logger.info("Flask application created (env=%s)", settings.environment)
    return app


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


def _register_request_hooks(app: Flask) -> None:
    """Attach before/after request hooks for correlation IDs and logging."""

    @app.before_request
    def assign_correlation_id():
        """Assign a correlation ID to every inbound request."""
        incoming = request.headers.get("X-Correlation-ID")
        cid = set_correlation_id(incoming)
        request.environ["correlation_id"] = cid

    @app.after_request
    def add_correlation_header(response):
        """Echo the correlation ID back in the response headers."""
        cid = get_current_correlation_id()
        if cid:
            response.headers["X-Correlation-ID"] = cid
        # Security headers
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


def _register_error_handlers(app: Flask) -> None:
    """Map exception types to consistent JSON error responses."""

    from core.exceptions import (
        KanyozaError,
        APIError,
        ValidationError,
        NotFoundError,
        RateLimitError,
        UnauthorizedError,
    )

    @app.errorhandler(KanyozaError)
    def handle_kanyoza_error(exc: KanyozaError):
        status = getattr(exc, "http_status", 500)
        logger.error(
            "KanyozaError: %s [%s]",
            exc.message,
            exc.error_code,
            extra={"context": exc.context},
        )
        return jsonify(exc.to_dict()), status

    @app.errorhandler(404)
    def handle_404(exc):
        return jsonify({"error": "NOT_FOUND", "message": "Resource not found."}), 404

    @app.errorhandler(405)
    def handle_405(exc):
        return jsonify({"error": "METHOD_NOT_ALLOWED", "message": "Method not allowed."}), 405

    @app.errorhandler(413)
    def handle_413(exc):
        return jsonify({"error": "PAYLOAD_TOO_LARGE", "message": "Request body too large."}), 413

    @app.errorhandler(500)
    def handle_500(exc):
        logger.exception("Unhandled 500 error")
        return jsonify({"error": "INTERNAL_ERROR", "message": "An internal error occurred."}), 500


# ---------------------------------------------------------------------------
# System routes
# ---------------------------------------------------------------------------


def _register_system_routes(app: Flask, settings: Settings) -> None:
    """Register lightweight health and status endpoints."""

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

    @app.route("/api/v1/status")
    def status():
        return jsonify({
            "version": "10.0",
            "config": settings.to_safe_dict(),
        })

    @app.route("/api/v1/health/deep")
    def health_deep():
        """
        Live connectivity probe for every external dependency.

        Returns per-service status:
          ok      — configured and reachable
          error   — configured but unreachable / auth failure
          skip    — not configured (degraded-mode expected)
        """
        import time

        results = {}
        overall = "ok"

        # ── Gemini ─────────────────────────────────────────────────────────────
        gemini_key = getattr(settings, "gemini_key", "") or ""
        if not gemini_key:
            results["gemini"] = {"status": "skip", "reason": "GEMINI_KEY not set"}
        else:
            try:
                from services.gemini import gemini as gemini_svc
                t0 = time.monotonic()
                reply = gemini_svc._call(
                    prompt="Reply with exactly one word: ok",
                    temperature=0.0,
                    max_tokens=8,
                )
                ms = round((time.monotonic() - t0) * 1000)
                if reply and reply.strip():
                    results["gemini"] = {"status": "ok", "latency_ms": ms}
                else:
                    results["gemini"] = {"status": "error", "reason": "empty response"}
                    overall = "degraded"
            except Exception as exc:
                results["gemini"] = {"status": "error", "reason": str(exc)[:120]}
                overall = "degraded"

        # ── Facebook Graph API ──────────────────────────────────────────────────
        fb_token = getattr(settings, "page_access_token", "") or ""
        if not fb_token:
            results["facebook"] = {"status": "skip", "reason": "PAGE_ACCESS_TOKEN not set"}
        else:
            try:
                import requests as _req
                t0 = time.monotonic()
                r = _req.get(
                    "https://graph.facebook.com/me",
                    params={"fields": "id,name", "access_token": fb_token},
                    timeout=8,
                )
                ms = round((time.monotonic() - t0) * 1000)
                if r.status_code == 200:
                    data = r.json()
                    results["facebook"] = {
                        "status": "ok",
                        "latency_ms": ms,
                        "page_id": data.get("id"),
                        "page_name": data.get("name"),
                    }
                else:
                    results["facebook"] = {
                        "status": "error",
                        "http": r.status_code,
                        "reason": r.json().get("error", {}).get("message", "unknown")[:120],
                    }
                    overall = "degraded"
            except Exception as exc:
                results["facebook"] = {"status": "error", "reason": str(exc)[:120]}
                overall = "degraded"

        # ── Supabase ────────────────────────────────────────────────────────────
        if not settings.supabase_enabled:
            results["supabase"] = {"status": "skip", "reason": "Supabase not configured"}
        else:
            try:
                from supabase import create_client
                sb_url = getattr(settings, "supabase_url", "") or ""
                sb_key = getattr(settings, "supabase_key", "") or ""
                t0 = time.monotonic()
                client = create_client(sb_url, sb_key)
                client.table("__health_check__").select("1").limit(1).execute()
                ms = round((time.monotonic() - t0) * 1000)
                results["supabase"] = {"status": "ok", "latency_ms": ms}
            except Exception as exc:
                msg = str(exc)[:120]
                if "does not exist" in msg or "relation" in msg:
                    ms = round((time.monotonic() - t0) * 1000)
                    results["supabase"] = {"status": "ok", "latency_ms": ms, "note": "connected"}
                else:
                    results["supabase"] = {"status": "error", "reason": msg}
                    overall = "degraded"

        return jsonify({
            "status": overall,
            "version": "10.0",
            "services": results,
        })


# ---------------------------------------------------------------------------
# v9 route compatibility shim
# ---------------------------------------------------------------------------


def _register_v9_routes(app: Flask) -> None:
    """
    Register existing v9 routes so the bot remains functional
    while v10 services are built in later milestones.
    Removed once all route blueprints are migrated to v10.
    """
    try:
        from routes.webhook import register_webhook_routes
        from routes.api import register_api_routes
        register_webhook_routes(app)
        register_api_routes(app)
        logger.info("v9 routes registered (compatibility shim active).")
    except Exception as exc:  # pragma: no cover
        logger.warning("v9 route registration skipped: %s", exc)
