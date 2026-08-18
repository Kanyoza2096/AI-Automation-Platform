"""
Kanyoza Systems AI Platform v14.7.2 — FastAPI Entry Point.

v14.7.2 FIXES:
  - Director routes mounted DIRECTLY for full/core/director mode
  - No proxy for in-process Director (eliminates 404/502 errors)
  - Proxy ONLY used when SERVICE_MODE=api (Director on separate instance)
  - Added /director/* routes directly from services.director module

v14.7.1 FIXES:
  - Director proxy DISABLED when Director runs in-process
  - Event API mounted
  - JobService health DEGRADED not UNHEALTHY
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import sys
import time
import warnings
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
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

# Suppress known library noise
logging.getLogger("supabase_auth").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", message=".*_refresh_token_timer.*")

# Director service URL (only used when SERVICE_MODE=api)
DIRECTOR_SERVICE_URL = os.getenv(
    "DIRECTOR_SERVICE_URL",
    "https://omnexa-director.onrender.com",
).strip().rstrip("/")


def _get_service_mode() -> str:
    """Get SERVICE_MODE from environment or settings."""
    mode = os.getenv("SERVICE_MODE", "").strip().lower()
    if not mode:
        mode = getattr(settings, "service_mode", "full")
    return mode


# ═══════════════════════════════════════════════════════════════════════════
# STARTUP METADATA
# ═══════════════════════════════════════════════════════════════════════════

_startup_started_at: Optional[datetime] = None
_startup_completed_at: Optional[datetime] = None
_startup_duration_seconds: float = 0.0
_boot_warnings: list[dict[str, Any]] = []


def _record_boot_warning(service: str, exc: Exception) -> None:
    """Record a startup warning for health reporting."""
    warning = {
        "service": service,
        "error": str(exc)[:500],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _boot_warnings.append(warning)
    logger.warning("Boot warning [%s]: %s", service, exc)


# ═══════════════════════════════════════════════════════════════════════════
# MIDDLEWARE
# ═══════════════════════════════════════════════════════════════════════════

class ApiCallCounterMiddleware(BaseHTTPMiddleware):
    """Tracks daily API call counts in Redis."""
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
    """Returns 503 until boot has completed."""
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
# CONFIGURATION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def _configure_middleware(app: FastAPI) -> None:
    """Register application middleware."""
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
    """Configure CORS according to environment."""
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
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Correlation-ID"],
        max_age=86400,
    )
    logger.info("CORS origins: %s", origins if len(origins) < 5 else f"{origins[:3]}... ({len(origins)} total)")


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

def _register_health_endpoints(app: FastAPI) -> None:
    """Register liveness, readiness and detailed health endpoints."""
    
    @app.get("/health")
    async def liveness_check():
        return {
            "status": "alive",
            "version": "14.7.2",
            "uptime_seconds": round(
                (datetime.now(timezone.utc) - _startup_started_at).total_seconds(), 1
            ) if _startup_started_at else 0,
        }

    @app.get("/health/live")
    async def live_check():
        return {"status": "alive", "version": "14.7.2"}

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
            name for name, report in service_health.items()
            if report.status == HealthStatus.UNHEALTHY
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
            auth_header = request.headers.get("Authorization", "")
            if not token and auth_header.startswith("Bearer "):
                token = auth_header[7:]
            master_token = getattr(settings, "master_api_token", "") or ""
            if not master_token or not token or not hmac.compare_digest(token, master_token):
                raise HTTPException(status_code=401, detail="Authentication required")
        return _build_health_response(app)


def _build_health_response(app: FastAPI) -> dict[str, Any]:
    """Build the detailed platform health response."""
    booted = getattr(app.state, "boot_completed", False)
    if not booted:
        return {
            "status": "starting",
            "version": "14.7.2",
            "uptime_seconds": 0,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    service_health = health_registry.check_all()
    statuses = [report.status for report in service_health.values()]

    if any(status == HealthStatus.UNHEALTHY for status in statuses):
        overall = "unhealthy"
    elif any(status == HealthStatus.DEGRADED for status in statuses):
        overall = "degraded"
    elif _boot_warnings:
        overall = "degraded"
    else:
        overall = "healthy"

    uptime = (
        (datetime.now(timezone.utc) - _startup_started_at).total_seconds()
        if _startup_started_at else 0
    )

    return {
        "status": overall,
        "version": "14.7.2",
        "uptime_seconds": round(uptime, 1),
        "boot_duration_seconds": round(_startup_duration_seconds, 3),
        "boot_warnings": len(_boot_warnings),
        "services": {name: report.to_dict() for name, report in service_health.items()},
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# EVENT API MOUNTING
# ═══════════════════════════════════════════════════════════════════════════

def _mount_event_api(app: FastAPI) -> None:
    """Mount Event API routes for real-time event observability."""
    try:
        from routes.event_api import register_event_api_routes
        register_event_api_routes(app)
        logger.info("Event API routes mounted (/api/v1/events/*).")
    except ImportError:
        logger.debug("Event API routes not available — skipping.")
    except Exception as exc:
        logger.warning("Event API mount failed: %s", str(exc)[:300])


# ═══════════════════════════════════════════════════════════════════════════
# DIRECTOR ROUTES (v14.7.2 — Direct Mount for In-Process)
# ═══════════════════════════════════════════════════════════════════════════

def _mount_director_routes_directly(app: FastAPI) -> None:
    """Mount Director routes DIRECTLY from services.director module."""
    try:
        from services.director import create_app as create_director_app
        from services.director import director
        
        if director is None:
            logger.warning("Director instance is None — routes not mounted")
            return
        
        director_app = create_director_app(director)
        app.mount("/director", director_app)
        logger.info("✅ Director routes mounted directly at /director/*")
        
    except ImportError as exc:
        logger.warning(f"Director module not available: {exc}")
    except Exception as exc:
        logger.warning(f"Director route mounting failed: {exc}")


def _mount_director_proxy(app: FastAPI) -> None:
    """Mount /director/* proxy ONLY when Director is on SEPARATE instance."""
    
    service_mode = _get_service_mode()
    
    # Director runs in-process — mount routes DIRECTLY
    if service_mode in ("full", "core", "director"):
        logger.info(
            "Director proxy DISABLED — Director runs in-process "
            "(SERVICE_MODE=%s)",
            service_mode,
        )
        _mount_director_routes_directly(app)
        return
    
    # Director on separate instance — use proxy
    logger.info(
        "Director proxy ENABLED: /director/* -> %s (SERVICE_MODE=%s)",
        DIRECTOR_SERVICE_URL,
        service_mode,
    )
    
    request_hop_by_hop_headers = {
        "host", "content-length", "connection", "transfer-encoding", "accept-encoding",
    }
    response_hop_by_hop_headers = {
        "content-length", "connection", "transfer-encoding", "content-encoding",
    }

    @app.api_route(
        "/director/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
    async def proxy_director(request: Request, path: str):
        target_url = f"{DIRECTOR_SERVICE_URL}/{path}"
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in request_hop_by_hop_headers
        }
        headers["accept-encoding"] = "identity"
        params = list(request.query_params.multi_items())
        body = await request.body()

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0),
                follow_redirects=False,
            ) as client:
                upstream = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    params=params,
                    content=body if body else None,
                )

            response_headers: dict[str, str] = {}
            for key, value in upstream.headers.items():
                if key.lower() in response_hop_by_hop_headers:
                    continue
                response_headers[key] = value

            response_headers.setdefault("Access-Control-Allow-Origin", "*")
            response_headers.setdefault("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH, OPTIONS, HEAD")
            response_headers.setdefault("Access-Control-Allow-Headers", "Authorization, Content-Type, X-API-Key, X-Correlation-ID")

            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                headers=response_headers,
            )

        except httpx.TimeoutException:
            logger.warning("Director proxy timeout: %s %s", request.method, request.url.path)
            return Response(
                content=json.dumps({"status": "error", "error": "Director service timeout"}),
                status_code=504,
                media_type="application/json",
            )
        except httpx.ConnectError:
            logger.warning("Director proxy unavailable: %s", DIRECTOR_SERVICE_URL)
            return Response(
                content=json.dumps({"status": "error", "error": "Director service unavailable"}),
                status_code=503,
                media_type="application/json",
            )
        except httpx.RequestError as exc:
            logger.warning("Director proxy request failed: %s", str(exc)[:200])
            return Response(
                content=json.dumps({"status": "error", "error": "Director service request failed"}),
                status_code=502,
                media_type="application/json",
            )
        except Exception:
            logger.exception("Unexpected Director proxy failure: %s", request.url.path)
            return Response(
                content=json.dumps({"status": "error", "error": "Director proxy failure"}),
                status_code=502,
                media_type="application/json",
            )


# ═══════════════════════════════════════════════════════════════════════════
# ROUTE MOUNTING
# ═══════════════════════════════════════════════════════════════════════════

def _mount_routes(app: FastAPI) -> None:
    """Mount core application routers."""
    route_modules = [
        ("API v1", "api.v1", "router"),
        ("Rule Engine", "routes.rule_routes", "router"),
        ("Meta Engine", "routes.meta_routes", "router"),
        ("Email Service", "routes.email_routes", "router"),
        ("Webhook", "routes.webhook", "router"),
    ]

    for name, module_path, attr in route_modules:
        try:
            module = __import__(module_path, fromlist=[attr])
            router = getattr(module, attr, None)
            if router:
                app.include_router(router)
                logger.info("%s routes mounted.", name)
        except ImportError:
            logger.debug("%s routes not available — skipping.", name)
        except Exception as exc:
            logger.warning("%s routes failed to mount: %s", name, str(exc)[:300])

    # Trend Intelligence routes
    try:
        from plugins.trend_intelligence.routes_public import router as trend_public_router
        app.include_router(trend_public_router)
        logger.info("Trend Intelligence public routes mounted.")
    except ImportError:
        logger.debug("Trend Intelligence public routes not available.")
    except Exception as exc:
        logger.warning("Trend Intelligence public routes mount failed: %s", str(exc)[:200])

    try:
        from plugins.trend_intelligence.routes_admin import router as trend_admin_router
        app.include_router(trend_admin_router)
        logger.info("Trend Intelligence admin routes mounted.")
    except ImportError:
        logger.debug("Trend Intelligence admin routes not available.")
    except Exception as exc:
        logger.warning("Trend Intelligence admin routes mount failed: %s", str(exc)[:200])

    # Backup routes
    try:
        from plugins.backup.routes_public import router as backup_public_router
        app.include_router(backup_public_router)
        logger.info("Backup public routes mounted.")
    except ImportError:
        logger.debug("Backup public routes not available.")
    except Exception as exc:
        logger.warning("Backup public routes mount failed: %s", str(exc)[:200])

    try:
        from plugins.backup.routes_admin import router as backup_admin_router
        app.include_router(backup_admin_router)
        logger.info("Backup admin routes mounted.")
    except ImportError:
        logger.debug("Backup admin routes not available.")
    except Exception as exc:
        logger.warning("Backup admin routes mount failed: %s", str(exc)[:200])

    _mount_plugin(app, "growthbridge", "Growthbridge")

    try:
        from routes.code_intel_routes import router as code_intel_router
        app.include_router(code_intel_router)
        logger.info("Code Intelligence routes mounted.")
    except ImportError:
        logger.debug("Code Intelligence routes not available.")
    except Exception as exc:
        logger.warning("Code Intelligence routes failed to mount: %s", str(exc)[:300])


def _mount_plugin(app: FastAPI, plugin_name: str, display_name: str) -> None:
    """Mount public and admin routes for an optional plugin."""
    try:
        public_module = __import__(
            f"plugins.{plugin_name}.routes.public", fromlist=["router"]
        )
        admin_module = __import__(
            f"plugins.{plugin_name}.routes.admin", fromlist=["router"]
        )
        public_router = getattr(public_module, "router", None)
        admin_router = getattr(admin_module, "router", None)

        if public_router:
            app.include_router(public_router)
        if admin_router:
            app.include_router(admin_router)
        logger.info("%s plugin routes mounted.", display_name)
    except ImportError:
        logger.debug("%s plugin not available — skipping.", display_name)
    except Exception as exc:
        logger.warning("%s mount failed: %s", display_name, str(exc)[:300])


# ═══════════════════════════════════════════════════════════════════════════
# POST-BOOT HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _resolve_redis(container: ServiceContainer):
    """Resolve Redis from the application container."""
    try:
        from core.redis_client import UpstashRedisClient
        redis = container.try_resolve(UpstashRedisClient)
        if redis:
            logger.info("Redis client resolved for wiring.")
        return redis
    except Exception as exc:
        logger.debug("Redis not available: %s", exc)
        return None


def _init_api_stores(app, redis_client) -> None:
    """Initialize API-local stores."""
    from api.v1 import (
        DraftStore, AsyncAuditLog, AsyncWorkflowHistory, AsyncKeyStore,
    )
    app.state.draft_store = DraftStore(redis_client=redis_client)
    app.state.audit_log = AsyncAuditLog(maxlen=200)
    app.state.workflow_history = AsyncWorkflowHistory(maxlen=20)
    app.state.key_store = AsyncKeyStore()

    try:
        asyncio.create_task(
            app.state.audit_log.seed_from_supabase(),
            name="audit-log-seed",
        )
    except Exception as exc:
        logger.debug("Audit log seeding skipped: %s", exc)

    logger.info("API stores initialized (DraftStore, AuditLog, WorkflowHistory, KeyStore).")


async def _draft_cleanup_loop(app) -> None:
    """Periodically clean expired drafts."""
    while True:
        try:
            await asyncio.sleep(3600)
            await app.state.draft_store.cleanup_expired()
            logger.debug("Draft cleanup completed.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Draft cleanup error: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# LIFESPAN
# ═══════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _startup_started_at, _startup_completed_at, _startup_duration_seconds

    _startup_started_at = datetime.now(timezone.utc)
    startup_start = time.monotonic()
    service_mode = _get_service_mode()
    _boot_warnings.clear()

    app.state.boot_completed = False
    app.state.boot_warnings = []

    logger.info("=" * 60)
    logger.info("Kanyoza AI Platform v14.7.2 — Starting up...")
    logger.info("  Environment : %s", settings.environment)
    logger.info("  Service Mode: %s", service_mode)
    logger.info("  Log level   : %s", settings.log_level)
    logger.info("  Python      : %s", _get_python_version())
    logger.info("=" * 60)

    settings.validate_required()
    logger.info("Building service container...")

    container: ServiceContainer = build_container()
    app.state.container = container
    redis_client = _resolve_redis(container)

    try:
        boot_infrastructure(app, container)
        logger.info("Phased boot sequence complete.")
    except SystemExit:
        raise
    except Exception as exc:
        _record_boot_warning("boot_sequence", exc)

    # Initialize Event API after boot
    if service_mode in ("full", "api", ""):
        try:
            from routes.event_api import init_event_api
            init_event_api()
            logger.info("Event API initialized — capturing live events.")
        except ImportError:
            logger.debug("Event API not available — event capture skipped.")
        except Exception as exc:
            logger.warning("Event API initialization failed: %s", str(exc)[:200])

    # API-local stores
    if service_mode in ("full", "api", ""):
        _init_api_stores(app, redis_client)

    cleanup_task: Optional[asyncio.Task] = None

    if service_mode in ("full", "api", ""):
        cleanup_task = asyncio.create_task(
            _draft_cleanup_loop(app), name="draft-cleanup-loop"
        )
        shutdown_registry.register(
            "DraftCleanup",
            lambda: cleanup_task.cancel() if cleanup_task and not cleanup_task.done() else None,
            timeout_seconds=5.0,
        )

    app.state.boot_completed = True
    app.state.boot_warnings = list(_boot_warnings)
    _startup_completed_at = datetime.now(timezone.utc)
    _startup_duration_seconds = time.monotonic() - startup_start

    if _boot_warnings:
        logger.warning("Boot completed with %d warning(s) in %.2fs:", len(_boot_warnings), _startup_duration_seconds)
        for warning in _boot_warnings:
            logger.warning("  ⚠ %s: %s", warning["service"], warning["error"])
    else:
        logger.info("Boot complete — all configured services initialized (%.2fs).", _startup_duration_seconds)

    logger.info("Application ready to accept requests.")
    logger.info("=" * 60)

    try:
        yield
    finally:
        logger.info("=" * 60)
        logger.info("Kanyoza AI Platform v14.7.2 — Shutting down...")
        logger.info("=" * 60)

        if cleanup_task and not cleanup_task.done():
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("Draft cleanup shutdown error: %s", str(exc)[:200])

        graceful_shutdown(container)
        app.state.boot_completed = False
        logger.info("Shutdown complete.")


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATION FACTORY
# ═══════════════════════════════════════════════════════════════════════════

def create_app() -> FastAPI:
    app = FastAPI(
        title="Kanyoza Systems AI Platform",
        description="Multi-tenant social media AI platform with industry plugins",
        version="14.7.2",
        lifespan=lifespan,
        docs_url="/docs" if settings.is_development else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.is_development else None,
    )

    app.state.boot_completed = False
    app.state.container = None
    app.state.boot_warnings = []

    _configure_middleware(app)
    _configure_cors(app)

    app.add_middleware(StartupGateMiddleware)
    app.add_middleware(ApiCallCounterMiddleware)

    _register_health_endpoints(app)
    _mount_routes(app)
    _mount_event_api(app)
    _mount_director_proxy(app)  # v14.7.2: Smart proxy — direct mount for full mode

    @app.get("/")
    @app.head("/")
    async def root():
        return {
            "status": "ok",
            "version": "14.7.2",
            "platform": "Kanyoza AI",
            "docs": "/docs" if settings.is_development else None,
            "events_api": "/api/v1/events",
            "director": "/director",
        }

    @app.get("/workspaces")
    async def get_workspaces():
        try:
            from services.supabase import get_service_client_from_settings
            client = get_service_client_from_settings()
            if client:
                result = client.table("workspaces").select("*").execute()
                return {"workspaces": result.data or []}
        except Exception as exc:
            logger.debug("Workspace lookup failed: %s", str(exc)[:200])
        return {"workspaces": []}

    return app


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATION ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════

fastapi_app = create_app()
app = create_socket_app(fastapi_app)


# ═══════════════════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════════════════

def _get_python_version() -> str:
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