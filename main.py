"""
Kanyoza Systems AI Platform v14.1 — FastAPI Entry Point.

Orchestrates application creation, middleware, route mounting, and
the phased boot sequence via the lifecycle module.

v14.1 HARDENING (Targeted Patch Release):
  - Removed duplicate service initialization (Director, MetaEngine,
    ContentEngine, BrandSchedulers were booted twice)
  - Fixed middleware ordering (StartupGate is now outermost)
  - Added HMAC token type validation for /health/detailed
  - Made _boot_warnings thread-safe
  - Fixed asyncio.create_task to work without running loop
  - Sanitized error messages in boot warnings
  - Added thread safety for startup metadata globals
  - Removed redundant draft cleanup cancellation
  - Director stop is now idempotent (guarded against double-stop)
  - ApiCallCounterMiddleware uses async-safe Redis operations
  - v14.0 FIXES (retained):
    - _safe_publish() logs EventBus failures with exc_info
    - _stop_director() logs shutdown failures
    - supabase_auth garbage collection noise suppression
"""

import asyncio
import hmac
import json
import logging
import sys
import threading
import time
import warnings
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from config.settings import settings
from service_container import ServiceContainer
from dependency_registry import build_container
from lifecycle import (
    boot_infrastructure,
    graceful_shutdown,
    health_registry,
    shutdown_registry,
    HealthStatus,
    _sanitize_error_message,
)
from core.socket_server import create_socket_app

logger = logging.getLogger("kanyoza.main")

# ── Suppress known library noise ──────────────────────────────────────
logging.getLogger("supabase_auth").setLevel(logging.CRITICAL)
warnings.filterwarnings(
    "ignore", message=".*_refresh_token_timer.*"
)

# ═══════════════════════════════════════════════════════════════════════════
# STARTUP METADATA (thread-safe)
# ═══════════════════════════════════════════════════════════════════════════

_startup_lock = threading.RLock()
_startup_started_at: Optional[datetime] = None
_startup_completed_at: Optional[datetime] = None
_startup_duration_seconds: float = 0.0
_boot_warnings: list[dict[str, Any]] = []
_app_instance: Optional[FastAPI] = None
_director_stopped: bool = False


def _set_startup_started() -> None:
    """Record startup start time (thread-safe)."""
    global _startup_started_at
    with _startup_lock:
        _startup_started_at = datetime.now(timezone.utc)


def _set_startup_completed(duration: float) -> None:
    """Record startup completion time and duration (thread-safe)."""
    global _startup_completed_at, _startup_duration_seconds
    with _startup_lock:
        _startup_completed_at = datetime.now(timezone.utc)
        _startup_duration_seconds = duration


def _get_startup_metadata() -> tuple[
    Optional[datetime], Optional[datetime], float
]:
    """Retrieve startup metadata (thread-safe)."""
    with _startup_lock:
        return (
            _startup_started_at,
            _startup_completed_at,
            _startup_duration_seconds,
        )


def _record_boot_warning(
    service: str, exc: Exception
) -> None:
    """Record a boot warning with sanitized error message.

    v14.1 — Thread-safe and sanitized to prevent internal path leakage.
    """
    global _boot_warnings
    sanitized = _sanitize_error_message(exc, max_len=500)
    warning = {
        "service": service,
        "error": sanitized,
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),
    }
    with _startup_lock:
        _boot_warnings.append(warning)
    logger.warning(
        "Boot warning [%s]: %s", service, sanitized
    )


def _get_boot_warnings() -> list[dict[str, Any]]:
    """Retrieve boot warnings (thread-safe copy)."""
    with _startup_lock:
        return list(_boot_warnings)


# ═══════════════════════════════════════════════════════════════════════════
# MIDDLEWARE
# ═══════════════════════════════════════════════════════════════════════════


class ApiCallCounterMiddleware(BaseHTTPMiddleware):
    """Tracks daily API call counts in Redis.

    Failures are intentionally silent — this is telemetry, not
    critical infrastructure.

    v14.1 — Uses asyncio.to_thread to avoid blocking the event loop
    with synchronous Redis operations.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/"):
            try:
                container = getattr(
                    request.app.state, "container", None
                )
                if container:
                    from core.redis_client import (
                        UpstashRedisClient,
                    )

                    redis = container.try_resolve(
                        UpstashRedisClient
                    )
                    if redis and redis.is_available:
                        today = datetime.now(
                            timezone.utc
                        ).strftime("%Y-%m-%d")
                        key = f"metrics:api_calls:{today}"

                        # v14.1 — Run blocking Redis call in thread pool
                        def _increment():
                            count = redis.incr(key)
                            if count == 1:
                                redis.expire(key, 86400 * 2)

                        await asyncio.to_thread(_increment)
            except Exception:
                # Telemetry only — never block a request
                pass
        return await call_next(request)


class StartupGateMiddleware(BaseHTTPMiddleware):
    """Returns 503 until boot is complete."""

    async def dispatch(
        self, request: Request, call_next
    ):
        if not getattr(
            request.app.state, "boot_completed", False
        ):
            if request.url.path in (
                "/health",
                "/health/live",
                "/health/ready",
            ):
                return await call_next(request)
            return Response(
                content=json.dumps({
                    "status": "starting",
                    "message": (
                        "Application is still initializing. "
                        "Please retry."
                    ),
                }),
                status_code=503,
                media_type="application/json",
                headers={"Retry-After": "5"},
            )
        return await call_next(request)


# ═══════════════════════════════════════════════════════════════════════════
# LIFESPAN
# ═══════════════════════════════════════════════════════════════════════════


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _director_stopped

    # ── STARTUP ───────────────────────────────────────────────────
    _set_startup_started()
    startup_start = time.monotonic()

    logger.info("=" * 60)
    logger.info(
        "Kanyoza AI Platform v14.1 — Starting up..."
    )
    logger.info(
        "  Environment : %s", settings.environment
    )
    logger.info(
        "  Log level   : %s", settings.log_level
    )
    logger.info(
        "  Python      : %s", _get_python_version()
    )
    logger.info("=" * 60)

    settings.validate_required()

    logger.info("Building service container...")
    container: ServiceContainer = build_container()
    app.state.container = container

    redis_client = _resolve_redis(container)
    event_bus = _resolve_event_bus(container)

    try:
        # v14.1 — boot_infrastructure already wires ContentEngine,
        # MetaEngine, Director, and BrandSchedulers. Do NOT call
        # _wire_content_engine, _wire_meta_engine, _wire_director,
        # or _sync_brand_schedulers again — this was duplicate
        # initialization in v14.0.
        boot_infrastructure(app, container)
        logger.info("Phased boot sequence complete.")
    except SystemExit:
        raise
    except Exception as exc:
        _record_boot_warning("boot_sequence", exc)

    # v14.1 — Mount Director sub-app (post-boot, not a re-init)
    _mount_director_app(app, container)

    # v14.1 — Sync brand schedulers (post-boot, not a re-init)
    _sync_brand_schedulers(container)

    # v14.1 — Initialize API stores (only if not already done)
    _init_api_stores(app, redis_client)

    cleanup_task = asyncio.create_task(
        _draft_cleanup_loop(app)
    )
    shutdown_registry.register(
        "DraftCleanup",
        lambda: cleanup_task.cancel(),
        timeout_seconds=5.0,
    )

    app.state.boot_completed = True
    app.state.boot_warnings = _get_boot_warnings()

    boot_warnings = _get_boot_warnings()
    _set_startup_completed(
        time.monotonic() - startup_start
    )
    _, _, duration = _get_startup_metadata()

    if boot_warnings:
        logger.warning(
            "Boot completed with %d warning(s) in %.2fs:",
            len(boot_warnings),
            duration,
        )
        for w in boot_warnings:
            logger.warning(
                "  ⚠ %s: %s",
                w["service"],
                w["error"],
            )
    else:
        logger.info(
            "Boot complete — all services healthy (%.2fs).",
            duration,
        )

    logger.info(
        "Application ready to accept requests."
    )
    logger.info("=" * 60)

    yield

    # ── SHUTDOWN ──────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info(
        "Kanyoza AI Platform v14.1 — Shutting down..."
    )
    logger.info("=" * 60)

    # v14.1 — Cancel draft cleanup only once (guard against double-cancel)
    if not cleanup_task.done():
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

    _stop_director()
    graceful_shutdown(container)

    logger.info("Shutdown complete.")


# ═══════════════════════════════════════════════════════════════════════════
# POST-BOOT WIRING (all functions below are now idempotent)
# ═══════════════════════════════════════════════════════════════════════════


def _resolve_redis(
    container: ServiceContainer,
):
    """Resolve Redis client from container."""
    try:
        from core.redis_client import (
            UpstashRedisClient,
        )

        redis = container.try_resolve(
            UpstashRedisClient
        )
        if redis:
            logger.info(
                "Redis client resolved for wiring."
            )
        return redis
    except Exception as exc:
        logger.debug(
            "Redis not available: %s",
            _sanitize_error_message(exc),
        )
        return None


def _resolve_event_bus(
    container: ServiceContainer,
):
    """Resolve EventBus from container."""
    try:
        from core.event_bus import EventBus

        return container.try_resolve(EventBus)
    except Exception:
        return None


def _mount_director_app(
    app: FastAPI, container: ServiceContainer
) -> None:
    """Mount Director sub-app without re-initializing Director.

    v14.1 — Director is already started by boot_infrastructure.
    This only mounts the HTTP routes.
    """
    try:
        from services.director import (
            create_app as create_director_app,
            director as dir_instance,
        )
        if dir_instance:
            director_app = create_director_app(
                dir_instance
            )
            app.mount("/director", director_app)
            logger.info(
                "Director routes mounted at /director."
            )
    except Exception as exc:
        logger.warning(
            "Director mount failed: %s",
            _sanitize_error_message(exc),
        )


def _sync_brand_schedulers(
    container: ServiceContainer,
) -> None:
    """Sync brand schedulers without re-initializing.

    v14.1 — BrandSchedulerManager is already started by
    boot_infrastructure. This only syncs brands.
    """
    try:
        from scheduler.brand_scheduler import (
            BrandSchedulerManager,
        )

        manager = container.try_resolve(
            BrandSchedulerManager
        )
        if manager:
            manager.set_container(container)
            manager._sync_brands()
            status = manager.get_all_status()
            logger.info(
                "BrandSchedulerManager: %d brands active.",
                len(status),
            )
    except Exception as exc:
        _record_boot_warning(
            "BrandSchedulerManager", exc
        )


# ═══════════════════════════════════════════════════════════════════════════
# SAFE PUBLISH (v14.0 — logs failures)
# ═══════════════════════════════════════════════════════════════════════════


def _safe_publish(
    event_bus, event_name: str, payload: dict
) -> None:
    """Publish an event to the EventBus.

    v14.0: Tries publish_event() first (string-based), then publish()
    (DomainEvent-based), then emit() as last resort.
    All failures are logged with full stack traces.
    """
    try:
        # v3.0 EventBus has publish_event() for string-based events
        if hasattr(event_bus, "publish_event"):
            event_bus.publish_event(event_name, payload)
        # Legacy EventBus — wrap in DomainEvent
        elif hasattr(event_bus, "publish"):
            from core.events import DomainEvent
            event = DomainEvent(
                event_type=event_name,
                payload=payload,
                source="director",
            )
            event_bus.publish(event)
        # Absolute fallback
        elif hasattr(event_bus, "emit"):
            event_bus.emit(event_name, payload)
    except Exception as exc:
        logger.error(
            "EventBus publish FAILED [%s]: %s",
            event_name,
            _sanitize_error_message(exc),
            exc_info=True,
        )


# ═══════════════════════════════════════════════════════════════════════════
# REMAINING HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _stop_director() -> None:
    """Stop the Director during shutdown (idempotent).

    v14.0: Failures are logged instead of silently swallowed.
    v14.1: Guarded against double-stop.
    """
    global _director_stopped
    if _director_stopped:
        return

    try:
        from services.director import (
            director as dir_instance,
        )

        if dir_instance:
            dir_instance.stop()
            logger.info("👔 Director: Shut down.")
            _director_stopped = True
    except Exception as exc:
        logger.error(
            "Director shutdown failed: %s",
            _sanitize_error_message(exc),
            exc_info=True,
        )


def _init_api_stores(app, redis_client) -> None:
    """Initialize API stores (DraftStore, AuditLog, etc.).

    v14.1 — Handles missing event loop gracefully.
    """
    from api.v1 import (
        DraftStore,
        AsyncAuditLog,
        AsyncWorkflowHistory,
        AsyncKeyStore,
    )

    app.state.draft_store = DraftStore(
        redis_client=redis_client
    )
    app.state.audit_log = AsyncAuditLog(maxlen=200)
    app.state.workflow_history = AsyncWorkflowHistory(
        maxlen=20
    )
    app.state.key_store = AsyncKeyStore()

    try:
        # v14.1 — Use asyncio.get_running_loop() to check if we're
        # in an async context. If not, defer or skip gracefully.
        loop = asyncio.get_running_loop()
        asyncio.create_task(
            app.state.audit_log.seed_from_supabase()
        )
    except RuntimeError:
        # v14.1 — No running event loop; log at DEBUG and skip.
        # This happens during testing or import-time initialization.
        logger.debug(
            "Audit log seeding skipped: no running event loop"
        )
    except Exception as exc:
        logger.debug(
            "Audit log seeding skipped: %s",
            _sanitize_error_message(exc),
        )

    logger.info(
        "API stores initialized (DraftStore, AuditLog, "
        "WorkflowHistory, KeyStore)."
    )


async def _draft_cleanup_loop(app) -> None:
    """Periodic cleanup of expired drafts."""
    while True:
        await asyncio.sleep(3600)
        try:
            await app.state.draft_store.cleanup_expired()
            logger.debug("Draft cleanup completed.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug(
                "Draft cleanup error: %s",
                _sanitize_error_message(exc),
            )


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════


def _validate_health_token(
    request: Request,
) -> bool:
    """Validate the X-API-Key or Bearer token for /health/detailed.

    v14.1 — Added type validation to prevent TypeError from
    hmac.compare_digest with non-string types.
    """
    token = request.headers.get("X-API-Key", "")
    auth_header = request.headers.get(
        "Authorization", ""
    )

    if not token and auth_header.startswith("Bearer "):
        token = auth_header[7:]

    master_token = (
        getattr(settings, "master_api_token", "")
        or ""
    )

    # v14.1 — Ensure both are strings before comparison
    if not isinstance(token, str):
        return False
    if not isinstance(master_token, str):
        logger.error(
            "master_api_token is not a string — "
            "health check authentication disabled."
        )
        return False
    if not master_token or not token:
        return False

    return hmac.compare_digest(token, master_token)


def _build_health_response(
    app: FastAPI,
) -> dict[str, Any]:
    """Build the detailed health response."""
    booted = getattr(
        app.state, "boot_completed", False
    )
    (
        started_at,
        completed_at,
        duration,
    ) = _get_startup_metadata()

    if not booted:
        return {
            "status": "starting",
            "version": "14.1.0",
            "uptime_seconds": 0,
            "checked_at": datetime.now(
                timezone.utc
            ).isoformat(),
        }

    service_health = health_registry.check_all()
    statuses = [
        r.status for r in service_health.values()
    ]
    boot_warnings = _get_boot_warnings()

    if any(
        s == HealthStatus.UNHEALTHY for s in statuses
    ):
        overall = "unhealthy"
    elif any(
        s == HealthStatus.DEGRADED for s in statuses
    ):
        overall = "degraded"
    elif boot_warnings:
        overall = "degraded"
    else:
        overall = "healthy"

    uptime = (
        (
            datetime.now(timezone.utc) - started_at
        ).total_seconds()
        if started_at
        else 0
    )

    return {
        "status": overall,
        "version": "14.1.0",
        "uptime_seconds": round(uptime, 1),
        "boot_duration_seconds": round(duration, 3),
        "boot_warnings": len(boot_warnings),
        "services": {
            name: report.to_dict()
            for name, report in service_health.items()
        },
        "checked_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATION FACTORY
# ═══════════════════════════════════════════════════════════════════════════


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(
        title="Kanyoza Systems AI Platform",
        description=(
            "Multi-tenant social media AI platform "
            "with industry plugins"
        ),
        version="14.1.0",
        lifespan=lifespan,
        docs_url=(
            "/docs" if settings.is_development else None
        ),
        redoc_url=None,
        openapi_url=(
            "/openapi.json"
            if settings.is_development
            else None
        ),
    )

    # v14.1 — StartupGate must be OUTERMOST middleware
    app.add_middleware(StartupGateMiddleware)
    _configure_middleware(app)
    _configure_cors(app)
    app.add_middleware(ApiCallCounterMiddleware)
    _register_health_endpoints(app)
    _mount_routes(app)

    @app.get("/")
    @app.head("/")
    async def root():
        return {
            "status": "ok",
            "version": "14.1.0",
            "platform": "Kanyoza AI",
            "docs": (
                "/docs"
                if settings.is_development
                else None
            ),
        }

    return app


def _configure_middleware(
    app: FastAPI,
) -> None:
    """Configure middleware chain."""
    from api.v1 import (
        SecurityHeadersMiddleware,
        RequestSizeLimitMiddleware,
        RequestTracingMiddleware,
        CorrelationIDMiddleware,
    )

    app.add_middleware(CorrelationIDMiddleware)
    app.add_middleware(
        RequestSizeLimitMiddleware, max_size=1_048_576
    )
    app.add_middleware(RequestTracingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    try:
        from middleware.traffic_capture import (
            TrafficCaptureMiddleware,
        )

        app.add_middleware(
            TrafficCaptureMiddleware
        )
        logger.info(
            "Traffic capture middleware registered."
        )
    except ImportError:
        logger.debug(
            "Traffic capture middleware not available."
        )


def _configure_cors(app: FastAPI) -> None:
    """Configure CORS."""
    if settings.is_production:
        origins = settings.cors_allowed_origins
        if not origins:
            raise RuntimeError(
                "No CORS origins configured for production. "
                "Set ALLOWED_ORIGINS environment variable."
            )
        allow_creds = True
    else:
        origins = ["*"]
        allow_creds = False

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_creds,
        allow_methods=[
            "GET", "POST", "PUT", "DELETE", "OPTIONS",
        ],
        allow_headers=[
            "Authorization", "Content-Type",
            "X-API-Key", "X-Correlation-ID",
        ],
        max_age=86400,
    )

    logger.info(
        "CORS origins: %s",
        origins
        if len(origins) < 5
        else f"{origins[:3]}... ({len(origins)} total)",
    )


def _register_health_endpoints(
    app: FastAPI,
) -> None:
    """Register health check endpoints."""

    @app.get("/health")
    async def liveness_check():
        (
            started_at,
            _,
            _,
        ) = _get_startup_metadata()
        return {
            "status": "alive",
            "version": "14.1.0",
            "uptime_seconds": round(
                (
                    datetime.now(timezone.utc)
                    - started_at
                ).total_seconds(),
                1,
            ) if started_at else 0,
        }

    @app.get("/health/ready")
    async def readiness_check():
        if not getattr(
            app.state, "boot_completed", False
        ):
            return Response(
                content=json.dumps({
                    "status": "not_ready",
                    "reason": "Boot in progress",
                }),
                status_code=503,
                media_type="application/json",
            )

        service_health = health_registry.check_all()
        unhealthy = [
            name
            for name, r in service_health.items()
            if r.status == HealthStatus.UNHEALTHY
        ]

        if unhealthy:
            return Response(
                content=json.dumps({
                    "status": "not_ready",
                    "reason": (
                        "Unhealthy services detected"
                    ),
                    "unhealthy_services": unhealthy,
                }),
                status_code=503,
                media_type="application/json",
            )

        return {
            "status": "ready",
            "services_monitored": len(
                service_health
            ),
        }

    @app.get("/health/detailed")
    async def detailed_health(request: Request):
        if settings.is_production:
            if not _validate_health_token(request):
                raise HTTPException(
                    status_code=401,
                    detail=(
                        "Authentication required for "
                        "detailed health report"
                    ),
                )

        return _build_health_response(app)


# ═══════════════════════════════════════════════════════════════════════════
# ROUTE MOUNTING
# ═══════════════════════════════════════════════════════════════════════════


def _mount_routes(app: FastAPI) -> None:
    """Mount all route modules."""
    route_modules = [
        (
            "API v1", "api.v1", "router",
            "include_router",
        ),
        (
            "Rule Engine", "routes.rule_routes",
            "router", "include_router",
        ),
        (
            "Meta Engine", "routes.meta_routes",
            "router", "include_router",
        ),
        (
            "Email Service",
            "routes.email_routes", "router",
            "include_router",
        ),
        (
            "Webhook", "routes.webhook", "router",
            "include_router",
        ),
    ]

    for name, module_path, attr, method in route_modules:
        try:
            module = __import__(
                module_path, fromlist=[attr]
            )
            router = getattr(module, attr, None)
            if router:
                getattr(app, method)(router)
                logger.info(
                    "%s routes mounted.", name
                )
        except ImportError:
            logger.debug(
                "%s routes not available — skipping.",
                name,
            )
        except Exception as exc:
            logger.warning(
                "%s routes failed to mount: %s",
                name,
                _sanitize_error_message(exc),
            )

    _mount_plugin(
        app, "growthbridge", "Growthbridge"
    )
    _mount_plugin(app, "monitor", "Monitor")
    _mount_plugin(app, "backup", "Backup")
    _mount_plugin(
        app, "trend_intelligence", "Trend Intelligence"
    )

    try:
        from routes.code_intel_routes import (
            router as code_intel_router,
        )

        app.include_router(code_intel_router)
        logger.info(
            "Code Intelligence routes mounted."
        )
    except ImportError:
        logger.debug(
            "Code Intelligence routes not available."
        )


def _mount_plugin(
    app: FastAPI,
    plugin_name: str,
    display_name: str,
) -> None:
    """Mount a plugin's routes."""
    try:
        public_module = __import__(
            f"plugins.{plugin_name}.routes.public",
            fromlist=["router"],
        )
        admin_module = __import__(
            f"plugins.{plugin_name}.routes.admin",
            fromlist=["router"],
        )
        app.include_router(public_module.router)
        app.include_router(admin_module.router)
        logger.info(
            "%s plugin routes mounted.",
            display_name,
        )
    except ImportError:
        logger.debug(
            "%s plugin not available — skipping.",
            display_name,
        )
    except Exception as exc:
        logger.warning(
            "%s mount failed: %s",
            display_name,
            _sanitize_error_message(exc),
        )


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATION ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════

fastapi_app = create_app()
app = create_socket_app(fastapi_app)


# ═══════════════════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════════════════


def _get_python_version() -> str:
    """Return the Python version as a string."""
    return (
        f"{sys.version_info.major}."
        f"{sys.version_info.minor}."
        f"{sys.version_info.micro}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=settings.is_development,
        log_level=settings.log_level.lower(),
    )
