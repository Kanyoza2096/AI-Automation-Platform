"""
Kanyoza Systems AI Platform v13.0 — FastAPI Entry Point.

Orchestrates application creation, middleware, route mounting, and
the phased boot sequence via the lifecycle module.

Run locally:
    python main.py

Run with Uvicorn (single worker, development):
    uvicorn main:app --host 0.0.0.0 --port 5000 --reload

Run with Gunicorn + UvicornWorker (production, multi-worker):
    gunicorn main:app \
        --worker-class uvicorn.workers.UvicornWorker \
        --workers 1 \
        --bind 0.0.0.0:$PORT \
        --timeout 120 \
        --graceful-timeout 30

Architecture:
  - Phased boot via lifecycle.boot_infrastructure()
  - Health reporting via lifecycle.health_registry (self-registering services)
  - Graceful shutdown via lifecycle.shutdown_registry
  - Kubernetes-native readiness/liveness probes at /health/ready and /health/live
"""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

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
)
from core.socket_server import create_socket_app

logger = logging.getLogger("kanyoza.main")

# ═══════════════════════════════════════════════════════════════════════════
# STARTUP METADATA — Used by health/readiness probes
# ═══════════════════════════════════════════════════════════════════════════

_startup_started_at: datetime | None = None
_startup_completed_at: datetime | None = None
_startup_duration_seconds: float = 0.0
_boot_warnings: list[dict[str, Any]] = []


# ═══════════════════════════════════════════════════════════════════════════
# MIDDLEWARE
# ═══════════════════════════════════════════════════════════════════════════


class ApiCallCounterMiddleware(BaseHTTPMiddleware):
    """Tracks daily API call counts in Redis with minimal overhead."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/"):
            try:
                container = getattr(request.app.state, "container", None)
                if container:
                    from core.redis_client import UpstashRedisClient
                    redis = container.try_resolve(UpstashRedisClient)
                    if redis and redis.is_available:
                        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        key = f"metrics:api_calls:{today}"
                        count = redis.incr(key)
                        if count == 1:
                            redis.expire(key, 86400 * 2)
            except Exception:
                pass
        return await call_next(request)


class StartupGateMiddleware(BaseHTTPMiddleware):
    """Returns 503 until boot is complete."""

    async def dispatch(self, request: Request, call_next):
        if not getattr(request.app.state, "boot_completed", False):
            if request.url.path in ("/health", "/health/live", "/health/ready"):
                return await call_next(request)
            return Response(
                content=json.dumps({
                    "status": "starting",
                    "message": "Application is still initializing. Please retry.",
                }),
                status_code=503,
                media_type="application/json",
                headers={"Retry-After": "5"},
            )
        return await call_next(request)


# ═══════════════════════════════════════════════════════════════════════════
# LIFESPAN — Phased startup and shutdown
# ═══════════════════════════════════════════════════════════════════════════


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager with phased boot sequence."""
    global _startup_started_at, _startup_completed_at, _startup_duration_seconds

    # ═══════════════════ STARTUP ═══════════════════════════════════════
    _startup_started_at = datetime.now(timezone.utc)
    startup_start = time.monotonic()

    logger.info("=" * 60)
    logger.info("Kanyoza AI Platform v13.0 — Starting up...")
    logger.info("  Environment : %s", settings.environment)
    logger.info("  Log level   : %s", settings.log_level)
    logger.info("  Python      : %s", _get_python_version())
    logger.info("=" * 60)

    settings.validate_required()

    logger.info("Building service container...")
    container: ServiceContainer = build_container()
    app.state.container = container

    redis_client = _resolve_redis(container)
    event_bus = _resolve_event_bus(container)

    try:
        boot_infrastructure(app, container)
        logger.info("Phased boot sequence complete.")
    except SystemExit:
        raise
    except Exception as exc:
        _record_boot_warning("boot_sequence", exc)

    _wire_content_engine(container, redis_client)
    _wire_meta_engine(container, event_bus)
    _wire_director(app, container, event_bus)

    # ── Mount Director sub-app AFTER director is initialized ──────────
    try:
        from services.director import create_app as create_director_app, director as dir_instance
        if dir_instance:
            director_app = create_director_app(dir_instance)
            app.mount("/director", director_app)
            logger.info("Director routes mounted at /director.")
    except Exception as exc:
        logger.warning("Director mount failed: %s", exc)

    _sync_brand_schedulers(container)
    _init_api_stores(app, redis_client)

    cleanup_task = asyncio.create_task(_draft_cleanup_loop(app))
    shutdown_registry.register(
        "DraftCleanup",
        lambda: cleanup_task.cancel(),
        timeout_seconds=5.0,
    )

    app.state.boot_completed = True
    app.state.boot_warnings = _boot_warnings

    _startup_completed_at = datetime.now(timezone.utc)
    _startup_duration_seconds = time.monotonic() - startup_start

    if _boot_warnings:
        logger.warning(
            "Boot completed with %d warning(s) in %.2fs:",
            len(_boot_warnings),
            _startup_duration_seconds,
        )
        for w in _boot_warnings:
            logger.warning("  ⚠ %s: %s", w["service"], w["error"])
    else:
        logger.info("Boot complete — all services healthy (%.2fs).", _startup_duration_seconds)

    logger.info("Application ready to accept requests.")
    logger.info("=" * 60)

    yield

    # ═══════════════════ SHUTDOWN ═════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Kanyoza AI Platform v13.0 — Shutting down...")
    logger.info("=" * 60)

    shutdown_start = time.monotonic()

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    _stop_director()
    graceful_shutdown(container)

    shutdown_duration = time.monotonic() - shutdown_start
    logger.info("Shutdown complete (%.2fs).", shutdown_duration)


# ═══════════════════════════════════════════════════════════════════════════
# POST-BOOT WIRING HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _resolve_redis(container: ServiceContainer):
    try:
        from core.redis_client import UpstashRedisClient
        redis = container.try_resolve(UpstashRedisClient)
        if redis:
            logger.info("Redis client resolved for wiring.")
        return redis
    except Exception as exc:
        logger.debug("Redis not available: %s", exc)
        return None


def _resolve_event_bus(container: ServiceContainer):
    try:
        from core.event_bus import EventBus
        return container.try_resolve(EventBus)
    except Exception:
        return None


def _wire_content_engine(container: ServiceContainer, redis_client) -> None:
    try:
        from services.content_engine import content_engine
        if redis_client:
            content_engine._redis = redis_client
        content_engine._container = container
        logger.info("ContentEngine: wired (Redis=%s)", redis_client is not None)
    except Exception as exc:
        _record_boot_warning("ContentEngine", exc)


def _wire_meta_engine(container: ServiceContainer, event_bus) -> None:
    try:
        from services.meta_engine import init_meta_engine
        meta = init_meta_engine(event_bus=event_bus, container=container)
        logger.info("MetaEngine v2.0: supervising %d engines", len(meta._engines))
    except Exception as exc:
        _record_boot_warning("MetaEngine", exc)


def _wire_director(app, container: ServiceContainer, event_bus) -> None:
    try:
        from services.director import init_director, auto_discover_engines

        supabase_client = None
        try:
            from services.supabase import get_service_client_from_settings
            supabase_client = get_service_client_from_settings()
        except Exception:
            logger.debug("Director: Supabase not available — in-memory mode.")

        director = init_director(supabase_client=supabase_client, auto_start=False)
        discovered = auto_discover_engines(director, container)
        _wire_director_alerts(director)

        if event_bus:
            director.set_event_callback(
                lambda event_name, payload: _safe_emit(event_bus, event_name, payload)
            )
            logger.info("Director: EventBus wired.")

        director.start()
        logger.info("👔 Director: %d engines under supervision.", discovered)
    except Exception as exc:
        _record_boot_warning("Director", exc)


def _wire_director_alerts(director) -> None:
    try:
        from services.email_service import email_service

        def alert_handler(payload: dict) -> None:
            subject = f"👔 Director Alert: {payload.get('type', 'Unknown')}"
            body = json.dumps(payload, indent=2, default=str)
            email_service.send_to_admins(subject=subject, body=body)

        director.set_alert_callback(alert_handler)
        logger.info("Director: Email alerts wired.")
    except Exception as exc:
        logger.debug("Director: Email alerts not wired: %s", exc)


def _safe_emit(event_bus, event_name: str, payload: dict) -> None:
    try:
        event_bus.emit(event_name, payload)
    except Exception:
        pass


def _sync_brand_schedulers(container: ServiceContainer) -> None:
    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        manager = container.try_resolve(BrandSchedulerManager)
        if manager:
            manager.set_container(container)
            manager._sync_brands()
            status = manager.get_all_status()
            logger.info("BrandSchedulerManager: %d brands active.", len(status))
    except Exception as exc:
        _record_boot_warning("BrandSchedulerManager", exc)


def _stop_director() -> None:
    try:
        from services.director import director as dir_instance
        if dir_instance:
            dir_instance.stop()
            logger.info("👔 Director: Shut down.")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# API STORE INITIALIZATION
# ═══════════════════════════════════════════════════════════════════════════


def _init_api_stores(app, redis_client) -> None:
    from api.v1 import DraftStore, AsyncAuditLog, AsyncWorkflowHistory, AsyncKeyStore

    app.state.draft_store = DraftStore(redis_client=redis_client)
    app.state.audit_log = AsyncAuditLog(maxlen=200)
    app.state.workflow_history = AsyncWorkflowHistory(maxlen=20)
    app.state.key_store = AsyncKeyStore()

    try:
        asyncio.create_task(app.state.audit_log.seed_from_supabase())
    except Exception as exc:
        logger.debug("Audit log seeding skipped: %s", exc)

    logger.info("API stores initialized (DraftStore, AuditLog, WorkflowHistory, KeyStore).")


async def _draft_cleanup_loop(app) -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            await app.state.draft_store.cleanup_expired()
            logger.debug("Draft cleanup completed.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Draft cleanup error: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH / READINESS / LIVENESS PROBES
# ═══════════════════════════════════════════════════════════════════════════


def _build_health_response() -> dict[str, Any]:
    booted = getattr(fastapi_app.state, "boot_completed", False)

    if not booted:
        return {
            "status": "starting",
            "version": "13.0.0",
            "uptime_seconds": 0,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    service_health = health_registry.check_all()
    statuses = [r.status for r in service_health.values()]

    if any(s == HealthStatus.UNHEALTHY for s in statuses):
        overall = "unhealthy"
    elif any(s == HealthStatus.DEGRADED for s in statuses):
        overall = "degraded"
    elif _boot_warnings:
        overall = "degraded"
    else:
        overall = "healthy"

    uptime = (datetime.now(timezone.utc) - _startup_started_at).total_seconds() if _startup_started_at else 0

    return {
        "status": overall,
        "version": "13.0.0",
        "uptime_seconds": round(uptime, 1),
        "boot_duration_seconds": round(_startup_duration_seconds, 3),
        "boot_warnings": len(_boot_warnings),
        "services": {
            name: report.to_dict()
            for name, report in service_health.items()
        },
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATION FACTORY
# ═══════════════════════════════════════════════════════════════════════════


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Kanyoza Systems AI Platform",
        description="Multi-tenant social media AI platform with industry plugins",
        version="13.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.is_development else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.is_development else None,
    )

    _configure_middleware(app)
    _configure_cors(app)
    app.add_middleware(StartupGateMiddleware)
    app.add_middleware(ApiCallCounterMiddleware)
    _register_health_endpoints(app)
    _mount_routes(app)

    @app.get("/")
    @app.head("/")
    async def root():
        return {
            "status": "ok",
            "version": "13.0.0",
            "platform": "Kanyoza AI",
            "docs": "/docs" if settings.is_development else None,
        }

    return app


def _configure_middleware(app: FastAPI) -> None:
    from api.v1 import (
        SecurityHeadersMiddleware,
        RequestSizeLimitMiddleware,
        RequestTracingMiddleware,
        CorrelationIDMiddleware,
    )

    app.add_middleware(CorrelationIDMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware, max_size=1_048_576)
    app.add_middleware(RequestTracingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    try:
        from middleware.traffic_capture import TrafficCaptureMiddleware
        app.add_middleware(TrafficCaptureMiddleware)
        logger.info("Traffic capture middleware registered.")
    except ImportError:
        logger.debug("Traffic capture middleware not available.")


def _configure_cors(app: FastAPI) -> None:
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
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Correlation-ID"],
        max_age=86400,
    )

    logger.info("CORS origins: %s", origins if len(origins) < 5 else f"{origins[:3]}... ({len(origins)} total)")


def _register_health_endpoints(app: FastAPI) -> None:
    @app.get("/health")
    async def liveness_check():
        return {
            "status": "alive",
            "version": "13.0.0",
            "uptime_seconds": round(
                (datetime.now(timezone.utc) - _startup_started_at).total_seconds(), 1
            ) if _startup_started_at else 0,
        }

    @app.get("/health/ready")
    async def readiness_check():
        if not getattr(app.state, "boot_completed", False):
            return Response(
                content=json.dumps({"status": "not_ready", "reason": "Boot in progress"}),
                status_code=503,
                media_type="application/json",
            )

        service_health = health_registry.check_all()
        unhealthy = [
            name for name, r in service_health.items()
            if r.status == HealthStatus.UNHEALTHY
        ]

        if unhealthy:
            return Response(
                content=json.dumps({
                    "status": "not_ready",
                    "reason": "Unhealthy services detected",
                    "unhealthy_services": unhealthy,
                }),
                status_code=503,
                media_type="application/json",
            )

        return {"status": "ready", "services_monitored": len(service_health)}

    @app.get("/health/detailed")
    async def detailed_health(request: Request):
        if settings.is_production:
            token = request.headers.get("X-API-Key", "")
            if not token and request.headers.get("Authorization", "").startswith("Bearer "):
                token = request.headers.get("Authorization", "")[7:]

            import hmac
            master_token = getattr(settings, "master_api_token", "") or ""
            if not master_token or not token or not hmac.compare_digest(token, master_token):
                raise HTTPException(status_code=401, detail="Authentication required for detailed health report")

        return _build_health_response()


# ═══════════════════════════════════════════════════════════════════════════
# ROUTE MOUNTING
# ═══════════════════════════════════════════════════════════════════════════


def _mount_routes(app: FastAPI) -> None:
    """Mount all route modules. Failures are logged but non-fatal."""
    route_modules = [
        ("API v1", "api.v1", "router", "include_router"),
        ("Rule Engine", "routes.rule_routes", "router", "include_router"),
        ("Meta Engine", "routes.meta_routes", "router", "include_router"),
        ("Email Service", "routes.email_routes", "router", "include_router"),
        ("Webhook", "routes.webhook", "router", "include_router"),
    ]

    for name, module_path, attr, method in route_modules:
        try:
            module = __import__(module_path, fromlist=[attr])
            router = getattr(module, attr, None)
            if router:
                getattr(app, method)(router)
                logger.info("%s routes mounted.", name)
        except ImportError:
            logger.debug("%s routes not available — skipping.", name)
        except Exception as exc:
            logger.warning("%s routes failed to mount: %s", name, exc)

    # Growthbridge plugin — business ecosystem
    try:
        from plugins.growthbridge.routes.public import router as gb_public
        from plugins.growthbridge.routes.admin import router as gb_admin
        app.include_router(gb_public)
        app.include_router(gb_admin)
        logger.info("Growthbridge plugin routes mounted.")
    except ImportError:
        logger.debug("Growthbridge plugin not available — skipping.")
    except Exception as exc:
        logger.warning("Growthbridge mount failed: %s", exc)

    # Monitor plugin — system administrator
    try:
        from plugins.monitor.routes.public import router as monitor_public
        from plugins.monitor.routes.admin import router as monitor_admin
        app.include_router(monitor_public)
        app.include_router(monitor_admin)
        logger.info("Monitor plugin routes mounted.")
    except ImportError:
        logger.debug("Monitor plugin not available — skipping.")
    except Exception as exc:
        logger.warning("Monitor mount failed: %s", exc)

    # Backup plugin — automated database backups
    try:
        from plugins.backup.routes.public import router as backup_public
        from plugins.backup.routes.admin import router as backup_admin
        app.include_router(backup_public)
        app.include_router(backup_admin)
        logger.info("Backup plugin routes mounted.")
    except ImportError:
        logger.debug("Backup plugin not available — skipping.")
    except Exception as exc:
        logger.warning("Backup mount failed: %s", exc)

    # Trend Intelligence plugin
    try:
        from plugins.trend_intelligence.routes.public import router as trends_public
        from plugins.trend_intelligence.routes.admin import router as trends_admin
        app.include_router(trends_public)
        app.include_router(trends_admin)
        logger.info("Trend Intelligence plugin routes mounted.")
    except ImportError:
        logger.debug("Trend Intelligence plugin not available — skipping.")
    except Exception as exc:
        logger.warning("Trend Intelligence mount failed: %s", exc)

    try:
        from routes.code_intel_routes import router as code_intel_router
        app.include_router(code_intel_router)
        logger.info("Code Intelligence routes mounted.")
    except ImportError:
        logger.debug("Code Intelligence routes not available — skipping.")


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATION ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════

fastapi_app = create_app()

# Wrap with SocketIO (must be the outermost layer)
app = create_socket_app(fastapi_app)


# ═══════════════════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════════════════


def _record_boot_warning(service: str, exc: Exception) -> None:
    _boot_warnings.append({
        "service": service,
        "error": str(exc),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    logger.warning("Boot warning [%s]: %s", service, exc)


def _get_python_version() -> str:
    import sys
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


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
